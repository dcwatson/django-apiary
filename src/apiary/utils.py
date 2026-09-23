import inspect
from collections.abc import Callable
from typing import Any, Literal

import msgspec
from django.conf import settings
from django.http import HttpRequest, HttpResponse, QueryDict
from django.utils.datastructures import MultiValueDict


def build_data(request: HttpRequest) -> QueryDict:
    """
    Returns a QueryDict of all data from the given request that the API should
    consider. By default, this always includes GET params. If the request method
    is not GET, it also includes either a JSON object from the request body, or
    form-encoded POST params.

    TODO: make a decision about whether POST should shadow/extend GET, of if it
    should be considered an error.
    """
    data = request.GET.copy()
    if request.method != "GET":
        if request.content_type == "application/json":
            json_data = msgspec.json.decode(request.body, type=dict, strict=False)
            data.update(json_data)
        else:
            data.update(request.POST)
    return data


def resolve_type(annotation: Any) -> tuple[msgspec.inspect.Type, bool]:
    """
    Given a type annotation, returns the corresponding *non-optional*
    `msgspec.inspect.Type` and whether the annotation is an optional type.
    """
    t = msgspec.inspect.type_info(annotation)
    optional = False
    if isinstance(t, (msgspec.inspect.AnyType, msgspec.inspect.NoneType)):
        optional = True
    elif isinstance(t, msgspec.inspect.UnionType) and t.includes_none:
        t = next(i for i in t.types if not isinstance(i, msgspec.inspect.NoneType))
        optional = True
    return t, optional


def build_args(
    handler: Callable,
    data: MultiValueDict,
    **url_params: Any,
) -> tuple[list, dict[str, Any]]:
    signature = inspect.signature(handler)
    # Skip over the first (request) parameter.
    params = list(signature.parameters.values())[1:]
    if hasattr(handler, "__self__"):
        handler_name = f"{handler.__self__.__class__.__qualname__}.{handler.__name__}"
    else:
        handler_name = handler.__qualname__
    handler_args = []
    handler_kwargs = {}
    # Keep track of remaining data keys, can we can pass them to **kwargs.
    remaining = set(data.keys())
    has_args = any(param.kind is param.VAR_POSITIONAL for param in params)
    named_params = {
        param.name
        for param in params
        if param.kind not in (param.VAR_POSITIONAL, param.VAR_KEYWORD)
    }
    for param in params:
        if param.kind is param.VAR_POSITIONAL:
            # If the handler takes *args, give it any path parameters without a named
            # handler argument.
            handler_args.extend(
                value for name, value in url_params.items() if name not in named_params
            )
            continue

        if param.kind is param.VAR_KEYWORD:
            # If the handler takes **kwargs, give it all the leftover raw values.
            for key in remaining:
                handler_kwargs[key] = data[key]
            remaining.clear()
            continue

        if param.annotation is param.empty and param.kind is not param.kind.VAR_KEYWORD:
            raise ValueError("Parameters in {handler_name} must be type-annotated.")

        t, _optional = resolve_type(param.annotation)
        if len(params) == 1 and hasattr(t, "fields") and param.name not in remaining:
            # Special case for single object parameter, parse the source data directly.
            value = msgspec.convert(data, param.annotation, strict=False)
            if param.kind is param.kind.POSITIONAL_ONLY:
                handler_args.append(value)
            else:
                handler_kwargs[param.name] = value
        elif param.kind is param.kind.POSITIONAL_ONLY:
            # Positional-only parameters are always assigned from the URL parameters.
            if param.name not in url_params:
                raise ValueError(
                    f"Unknown URL parameter in {handler_name}: {param.name}"
                )
            handler_args.append(url_params[param.name])
        elif param.name in url_params:
            if param.kind is param.kind.KEYWORD_ONLY:
                handler_kwargs[param.name] = url_params[param.name]
            else:
                handler_args.append(url_params[param.name])
        elif param.name in remaining:
            if isinstance(t, msgspec.inspect.CollectionType):
                # If the annotation is a collection, use getlist.
                value = data.getlist(param.name)
            else:
                # Otherwise use the last defined value.
                value = data[param.name]
            value = msgspec.convert(value, param.annotation, strict=False)
            if has_args and param.kind is param.kind.POSITIONAL_OR_KEYWORD:
                # Need to pass as positional since this param is before *args.
                handler_args.append(value)
            else:
                handler_kwargs[param.name] = value
            remaining.remove(param.name)
        elif param.default is param.empty:
            raise ValueError(
                f"Value missing in {handler_name} for `{param.name}` with no default."
            )
        elif has_args and param.kind is param.kind.POSITIONAL_OR_KEYWORD:
            handler_args.append(param.default)
    return handler_args, handler_kwargs


class FastJsonResponse(HttpResponse):
    def __init__(
        self,
        data: Any,
        enc_hook: Callable | None = None,
        order: Literal["deterministic", "sorted"] | None = None,
        indent: int | None = None,
        **kwargs,
    ):
        kwargs.setdefault("content_type", "application/json")
        content = msgspec.json.encode(data, enc_hook=enc_hook, order=order)
        if indent is None and settings.DEBUG:
            indent = 2
        if indent is not None and indent > 0:
            content = msgspec.json.format(content, indent=indent)
        super().__init__(content=content, **kwargs)
