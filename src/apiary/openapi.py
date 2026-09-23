"""OpenAPI generation from a Route and its middleware tree."""

import inspect
from collections.abc import Generator
from typing import Any

import msgspec
from django.http import HttpRequest, HttpResponseBase

from .base import PATH_PARAM_REGEX, APIMiddleware, APIView, Route


class OpenAPIView(APIView):
    """Serve a Route's schema, including when opened directly in a browser."""

    route: Route | None = None
    title: str = "API"
    version: str = "1.0.0"
    serve_docs = False

    def get(self, request: HttpRequest) -> dict:
        if self.route is None:
            raise ValueError("OpenAPIView requires a route.")
        return build_openapi(
            self.route,
            title=self.title,
            version=self.version,
            request=request,
        )


def _endpoints(
    middleware: APIMiddleware, prefix: str = ""
) -> Generator[tuple[Route, str]]:
    if isinstance(middleware, Route):
        prefix += middleware.path
        if middleware.view_class is not None:
            yield middleware, prefix
    for child in middleware.children:
        yield from _endpoints(child, prefix)


def _handlers(root: Route):
    for route, url_pattern in _endpoints(root):
        if route.view_class is None or issubclass(route.view_class, OpenAPIView):
            continue
        view = route.view_class(url_pattern=url_pattern, **route.view_kwargs)
        for method in view.http_method_names:
            handler = getattr(view, method, None)
            if method != "options" and callable(handler):
                yield route, url_pattern, view, method, handler


def build_openapi(
    root: Route,
    *,
    title: str = "API",
    version: str = "1.0.0",
    base_path: str = "/",
    request: HttpRequest | None = None,
) -> dict:
    paths = {}
    schema_placeholders = []
    query_objects = []
    header_objects = []

    def reference(annotation, default=inspect.Parameter.empty):
        # Each use gets its own placeholder so defaults never leak between inputs.
        schema = {}
        schema_placeholders.append((annotation, schema, default))
        return schema

    for route, url_pattern, view, method, handler in _handlers(root):
        path = base_path + PATH_PARAM_REGEX.sub(r"{\g<name>}", url_pattern).lstrip("/")
        # Use a list instead of a set here to maintain order.
        path_params = [m["name"] for m in PATH_PARAM_REGEX.finditer(url_pattern)]
        signature = inspect.signature(handler)
        handler_params = list(signature.parameters.values())[1:]
        default_source = "query" if method in ("get", "head") else "body"
        parameters = []
        query_object = None
        body = {"type": "object", "properties": {}}
        required = []
        has_body = False
        unpack_body = False
        missing = set(path_params)
        has_args = False

        header_fields = msgspec.inspect.type_info(view.Headers).fields
        header_schema = reference(view.Headers) if header_fields else None
        for field in header_fields:
            schema = {}
            header_objects.append((schema, header_schema, field.encode_name))
            parameters.append(
                {
                    "name": field.encode_name.replace("_", "-"),
                    "in": "header",
                    "required": field.required,
                    "schema": schema,
                }
            )

        for param in handler_params:
            if param.kind == param.VAR_KEYWORD:
                if default_source == "body":
                    body["additionalProperties"] = True
                    has_body = True
                continue
            if param.kind == param.VAR_POSITIONAL:
                has_args = True
                continue
            if param.annotation is param.empty:
                raise ValueError("Handler parameters must be type-annotated.")
            if param.name in path_params:
                source = "path"
                missing.discard(param.name)
            elif param.name in (route.url_kwargs or {}):
                continue
            else:
                source = default_source
            unpack = (
                source != "path"
                and len(handler_params) == 1
                and hasattr(msgspec.inspect.type_info(param.annotation), "fields")
            )
            is_required = source == "path" or param.default is param.empty
            default = param.default if not is_required and not unpack else param.empty
            schema = reference(param.annotation, default)
            if source == "body":
                has_body = True
                if unpack:
                    body = schema
                    unpack_body = True
                else:
                    body["properties"][param.name] = schema
                if is_required:
                    required.append(param.name)
            elif unpack:
                query_object = schema
            else:
                parameters.append(
                    {
                        "name": param.name,
                        "in": source,
                        "required": is_required,
                        "schema": schema,
                    }
                )
        if missing and not has_args:
            raise ValueError(
                f"Path parameters must be declared on the handler: {', '.join(sorted(missing))}"
            )
        for name in path_params:
            if name in missing:
                parameters.append(
                    {"name": name, "in": "path", "required": True, "schema": {}}
                )
        response: dict[str, Any] = {"description": "Successful response"}
        response_type = signature.return_annotation
        if response_type is not signature.empty and not (
            isinstance(response_type, type)
            and issubclass(response_type, HttpResponseBase)
        ):
            response["content"] = {
                "application/json": {"schema": reference(response_type)}
            }
        operation: dict[str, Any] = {"responses": {"200": response}}
        if doc := inspect.getdoc(handler):
            operation["description"] = doc
        if route.name:
            operation["operationId"] = f"{route.name}-{method}"
        if parameters:
            operation["parameters"] = parameters
        if query_object is not None:
            query_objects.append((operation, query_object))
        if has_body:
            if required and not unpack_body:
                body["required"] = required
            operation["requestBody"] = {
                "required": bool(required),
                "content": {
                    "application/json": {"schema": body},
                    "application/x-www-form-urlencoded": {"schema": body},
                },
            }
        paths.setdefault(path, {})[method] = operation
    # Resolve placeholders together to keep component names consistent across handlers.
    schemas, components = msgspec.json.schema_components(
        [annotation for annotation, _, _ in schema_placeholders],
        ref_template="#/components/schemas/{name}",
    )
    for (_, target, default), schema in zip(schema_placeholders, schemas):
        target.update(schema)
        if default is not inspect.Parameter.empty:
            target["default"] = msgspec.to_builtins(default)

    # Header fields share the schemas generated for their containing type.
    for target, schema, name in header_objects:
        if "$ref" in schema:
            schema = components[schema["$ref"].rsplit("/", 1)[1]]
        target.update(schema["properties"][name])

    # Structured queries can be expanded once their fields are available.
    for operation, schema in query_objects:
        if "$ref" in schema:
            schema = components[schema["$ref"].rsplit("/", 1)[1]]
        for name, prop in schema.get("properties", {}).items():
            operation.setdefault("parameters", []).append(
                {
                    "name": name,
                    "in": "query",
                    "schema": prop,
                    "required": name in schema.get("required", []),
                }
            )
    spec = {
        "openapi": "3.1.0",
        "info": {"title": title, "version": version},
        "paths": paths,
        "components": {"schemas": components},
    }
    if request:
        spec["servers"] = [
            {"url": request._current_scheme_host},
        ]
    return spec
