import inspect
import re
import warnings
from collections.abc import Awaitable, Callable, Generator
from functools import cached_property
from typing import Any

import msgspec
from django.http import HttpRequest, HttpResponseBase, QueryDict
from django.shortcuts import render
from django.urls import URLPattern, URLResolver, include, path
from django.views import View

from .exceptions import APIError
from .utils import FastJsonResponse, build_args, build_data

PATH_PARAM_REGEX = re.compile(r"<(?:[^:>]+:)?(?P<name>[^>]+)>")


class APIMiddleware:
    children: tuple[APIMiddleware, ...]

    def __init__(self, *children: APIMiddleware):
        self.children = children

    def __call__(self, *children: APIMiddleware):
        self.children += children
        return self

    def on_request(self, request: HttpRequest) -> HttpResponseBase | None:
        """
        Called at the start of a request. This method may short-circuit API request
        handling by returning an HttpResponseBase.
        """

    def on_response(
        self,
        request: HttpRequest,
        response: HttpResponseBase,
    ) -> HttpResponseBase | None:
        """
        Called at the end of a request, after a response has been generated (but not yet
        returned). This method may return its own HttpResponseBase.
        """

    def urlpatterns(
        self, stack: tuple[APIMiddleware, ...]
    ) -> Generator[URLResolver | URLPattern]:
        """
        A generator yielding Django URLResolver or URLPattern objects.
        """
        for child in self.children:
            yield from child.urlpatterns(stack + (self,))

    def to_urlpatterns(
        self, stack: tuple[APIMiddleware, ...] = ()
    ) -> list[URLResolver | URLPattern]:
        """
        A convenience method to return a list instead of a generator.
        """
        return list(self.urlpatterns(stack))

    def to_urlpattern(
        self, stack: tuple[APIMiddleware, ...] = ()
    ) -> list[URLResolver | URLPattern]:
        """
        A convenience method to return a single URL pattern. Will raise a RuntimeError
        if this middleware defines more or less than 1 pattern.
        """
        patterns = self.to_urlpatterns(stack)
        if len(patterns) != 1:
            raise RuntimeError(f"APIMiddleware defines {len(patterns)} patterns!")
        return patterns[0]


class APIView(View):
    """
    Base class for all API views.
    """

    middleware: tuple[APIMiddleware, ...] = ()
    url_pattern: str = ""
    serve_docs: bool = False

    request: HttpRequest

    class Headers(msgspec.Struct):
        pass

    @cached_property
    def headers(self) -> Headers:
        data = {}
        for field in msgspec.inspect.type_info(self.Headers).fields:
            header_name = field.encode_name.replace("_", "-")
            if header_name in self.request.headers:
                data[field.encode_name] = self.request.headers[header_name]
        return msgspec.convert(data, self.Headers, strict=False)

    @classmethod
    def check(cls, path: str, **kwargs):
        """
        Checks for errors in the handler definitions or URL path when generating
        Django URLPatterns.
        """
        for method in cls.http_method_names:
            handler = getattr(cls, method, None)
            if handler is None:
                continue
            # Pull out all the path parameter names, so we can check positional args.
            path_params = {match["name"] for match in PATH_PARAM_REGEX.finditer(path)}
            signature = inspect.signature(handler)
            # Skip the `self` parameter.
            params = list(signature.parameters.values())[1:]
            req_param = params.pop(0)
            handler_name = f"{cls.__name__}.{method}"
            has_args = False
            if not (req_param.annotation is HttpRequest or req_param.name == "request"):
                warnings.warn(
                    f"First parameter of {handler_name} should be named `request` or "
                    "annotated as `HttpRequest`",
                    stacklevel=2,
                )
            for param in params:
                # Everything except *args and **kwargs must be type-annotated.
                if param.annotation is param.empty and param.kind not in (
                    param.kind.VAR_KEYWORD,
                    param.kind.VAR_POSITIONAL,
                ):
                    raise TypeError(
                        f"{handler_name}.{param.name} must be type-annotated."
                    )
                if (
                    param.kind is param.kind.POSITIONAL_ONLY
                    and param.name not in path_params
                ):
                    # Positional-only args are always pulled from the URL path params.
                    raise TypeError(
                        f"Unknown URL parameter: {handler_name}.{param.name}"
                    )
                if param.kind is param.kind.VAR_POSITIONAL:
                    has_args = True
                path_params.discard(param.name)
            # Check to see if there are any path params without a matching handler
            # parameter (or *args).
            if path_params and not has_args:
                raise TypeError(
                    f"Unhandled URL parameters in {handler_name}: {path_params}"
                )

    def make_response(self, return_value: Any) -> HttpResponseBase:
        if isinstance(return_value, HttpResponseBase):
            return return_value
        order = "sorted" if self.request.headers.get("x-json-sorted") else None
        return FastJsonResponse(msgspec.to_builtins(return_value), order=order)

    def error_response(self, exception: Exception) -> HttpResponseBase:
        status_code = 500
        error_data = {"error": str(exception)}
        if isinstance(exception, APIError):
            status_code = exception.status_code
            error_data.update(exception.extra)
        return FastJsonResponse(error_data, status=status_code)

    def get_data(self, request: HttpRequest) -> QueryDict:
        """
        Returns a QueryDict of all data the API should consider, using `build_data` by
        default. Subclasses can override to customize the behavior.
        """
        return build_data(request)

    def process_request(self, request: HttpRequest) -> HttpResponseBase | None:
        """
        Calls `on_request` for each APIMiddleware assigned to this view (in the order
        they were defined), short-circuiting if any return an HttpResponseBase.
        """
        for middleware in self.middleware:
            if response := middleware.on_request(request):
                return response

    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        """
        Calls `on_response` for each APIMiddleware assigned to this view (in reverse
        order), short-circuiting if any return an HttpResponseBase.
        """
        for middleware in reversed(self.middleware):
            if new_response := middleware.on_response(request, response):
                return new_response
        return response

    def call_handler(
        self,
        request: HttpRequest,
        handler: Callable,
    ) -> HttpResponseBase | Awaitable:
        if self.view_is_async:

            async def async_handler():
                try:
                    if response := self.process_request(request):
                        return response
                    source_data = self.get_data(request)
                    handler_args, handler_kwargs = build_args(
                        handler, source_data, **self.kwargs
                    )
                    return_value = await handler(
                        request,
                        *handler_args,
                        **handler_kwargs,
                    )
                    response = self.make_response(return_value)
                except Exception as ex:  # noqa
                    response = self.error_response(ex)
                return self.process_response(request, response)

            return async_handler()
        else:
            try:
                if response := self.process_request(request):
                    return response
                source_data = self.get_data(request)
                handler_args, handler_kwargs = build_args(
                    handler, source_data, **self.kwargs
                )
                return_value = handler(request, *handler_args, **handler_kwargs)
                response = self.make_response(return_value)
            except Exception as ex:  # noqa
                response = self.error_response(ex)
            return self.process_response(request, response)

    def docs(self, request: HttpRequest, *args, **kwargs):
        response = render(request, "apiary/docs.html", {})

        if self.view_is_async:

            async def func():
                return response

            return func()
        else:
            return response

    def dispatch(self, request: HttpRequest, *args, **kwargs):
        is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"
        if (
            self.serve_docs
            and request.META.get("HTTP_ACCEPT", "").startswith("text/html")
            and not is_ajax
            and not request.GET
        ):
            return self.docs(request, *args, **kwargs)

        method = request.method.lower()
        handler = getattr(self, method, None)

        if method not in self.http_method_names or handler is None:
            return self.http_method_not_allowed(request, *args, **kwargs)

        return self.call_handler(request, handler)


class Route(APIMiddleware):
    def __init__(
        self,
        path: str,
        view_class: type[APIView] | None = None,
        url_kwargs: dict[str, Any] | None = None,
        name: str | None = None,
        **view_kwargs: Any,
    ):
        super().__init__()
        self.path = path
        self.view_class = view_class
        self.url_kwargs = url_kwargs
        self.name = name
        self.view_kwargs = view_kwargs

    def urlpatterns(self, stack: tuple[APIMiddleware, ...]):
        # If this is an actual endpoint, yield a Django path.
        if self.view_class:
            url_pattern = (
                "".join(m.path for m in stack if isinstance(m, Route)) + self.path
            )
            self.view_class.check(url_pattern)
            yield path(
                self.path,
                self.view_class.as_view(
                    middleware=stack,
                    url_pattern=url_pattern,
                    **self.view_kwargs,
                ),
                kwargs=self.url_kwargs,
                name=self.name,
            )
        # Gather the patterns for any children of this route.
        patterns = []
        new_stack = stack + (self,)
        for child in self.children:
            patterns.extend(child.urlpatterns(new_stack))
        # If there are any, yield a Django path with an include.
        if patterns:
            yield path(self.path, include(patterns))


def api_endpoint(
    path: str,
    view_class: type[APIView],
    url_kwargs: dict[str, Any] | None = None,
    name: str | None = None,
    **view_kwargs: Any,
):
    return Route(
        path,
        view_class,
        url_kwargs=url_kwargs,
        name=name,
        **view_kwargs,
    ).to_urlpattern()


def api_path(
    route: str,
    view: Callable,
    kwargs: dict[str, Any] | None = None,
    name: str | None = None,
):
    def handler(request, *args, **kwargs):
        source_data = build_data(request)
        handler_args, handler_kwargs = build_args(view, source_data, **kwargs)
        return view(request, *handler_args, **handler_kwargs)

    return path(route, handler, kwargs=kwargs, name=name)
