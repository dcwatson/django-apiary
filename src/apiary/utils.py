import inspect
import ipaddress
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, get_args, get_origin

import msgspec
from django.conf import settings
from django.http import HttpRequest, HttpResponse, QueryDict
from django.utils.datastructures import MultiValueDict


@dataclass(frozen=True)
class Alias:
    """The external name of a handler parameter in request data or a URL path."""

    name: str


def parameter_name(param: inspect.Parameter) -> str:
    if get_origin(param.annotation) is Annotated:
        for metadata in reversed(get_args(param.annotation)[1:]):
            if isinstance(metadata, Alias):
                return metadata.name
    return param.name


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
            for key, value in json_data.items():
                # If the top level value is a list, extend instead of appending a list.
                list_values = data.setlistdefault(key)
                if isinstance(value, list):
                    list_values.extend(value)
                else:
                    list_values.append(value)
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


def data_for_fields(
    data: MultiValueDict,
    fields: tuple[msgspec.inspect.Field, ...],
) -> dict[str, Any]:
    """
    Given some request data, and a number of fields that the data should be unpacked
    into, returns a dict suitable for passing to `msgspec.convert`. Specifically, this
    takes into account whether the keys should be read as lists or single values.
    """
    field_data = {}
    for field in fields:
        if field.encode_name not in data:
            continue
        if isinstance(field.type, msgspec.inspect.CollectionType):
            # If field is a collection, use getlist.
            value = data.getlist(field.encode_name)
        else:
            # Otherwise use the last defined value.
            value = data[field.encode_name]
        field_data[field.encode_name] = value
    return field_data


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
        param.name: parameter_name(param)
        for param in params
        if param.kind not in (param.VAR_POSITIONAL, param.VAR_KEYWORD)
    }
    for param in params:
        if param.kind is param.VAR_POSITIONAL:
            # If the handler takes *args, give it any path parameters without a named
            # handler argument.
            handler_args.extend(
                value
                for name, value in url_params.items()
                if name not in named_params.values()
            )
            continue

        if param.kind is param.VAR_KEYWORD:
            # If the handler takes **kwargs, give it all the leftover raw values.
            for key in remaining:
                # An aliased parameter's Python name must not bypass conversion
                # or overwrite the value read from its external name.
                if key in named_params:
                    continue
                handler_kwargs[key] = data[key]
            remaining.clear()
            continue

        if param.annotation is param.empty and param.kind is not param.kind.VAR_KEYWORD:
            raise ValueError("Parameters in {handler_name} must be type-annotated.")

        name = named_params[param.name]
        t, _optional = resolve_type(param.annotation)
        if len(params) == 1 and hasattr(t, "fields") and name not in remaining:
            # Special case for single object parameter, parse the source data directly.
            field_data = data_for_fields(data, t.fields)
            value = msgspec.convert(field_data, param.annotation, strict=False)
            if param.kind is param.kind.POSITIONAL_ONLY:
                handler_args.append(value)
            else:
                handler_kwargs[param.name] = value
        elif param.kind is param.kind.POSITIONAL_ONLY:
            # Positional-only parameters are always assigned from the URL parameters.
            if name in url_params:
                handler_args.append(url_params[name])
            elif param.default is not param.empty:
                handler_args.append(param.default)
            else:
                raise ValueError(
                    f"Unknown URL parameter in {handler_name}: {param.name}"
                )
        elif name in url_params:
            if param.kind is param.kind.KEYWORD_ONLY:
                handler_kwargs[param.name] = url_params[name]
            else:
                handler_args.append(url_params[name])
        elif name in remaining:
            if isinstance(t, msgspec.inspect.CollectionType):
                # If the annotation is a collection, use getlist.
                value = data.getlist(name)
            else:
                # Otherwise use the last defined value.
                value = data[name]
            value = msgspec.convert(value, param.annotation, strict=False)
            if has_args and param.kind is param.kind.POSITIONAL_OR_KEYWORD:
                # Need to pass as positional since this param is before *args.
                handler_args.append(value)
            else:
                handler_kwargs[param.name] = value
            remaining.remove(name)
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


def get_ip(request: HttpRequest) -> str:
    ip_address = request.META.get("HTTP_X_FORWARDED_FOR", "").strip()
    if ip_address:
        ip_address = ip_address.split(",")[0].strip()
    if not ip_address:
        ip_address = request.META.get("REMOTE_ADDR", "127.0.0.1").strip()
    try:
        # Validate and normalize the IP address.
        return str(ipaddress.ip_address(ip_address))
    except ValueError:
        return ""


def make_struct[T: msgspec.Struct](
    obj_type: type[T],
    strict: bool = True,
    **fields: Any,
) -> T:
    return msgspec.convert(fields, type=obj_type, strict=strict)
