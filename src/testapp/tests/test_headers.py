import uuid
from dataclasses import dataclass, field
from typing import Annotated, NotRequired, TypedDict

import msgspec
from django.test import RequestFactory, SimpleTestCase

from apiary import APIView, Route
from apiary.openapi import build_openapi


class HeaderTests(SimpleTestCase):
    def test_dataclass_headers(self):
        class HeaderView(APIView):
            @dataclass
            class Headers:
                x_count: Annotated[int, msgspec.Meta(ge=1)]
                x_limit: int = 10
                x_values: list[str] = field(default_factory=list)

            def get(self, request):
                return {}

        view = HeaderView()
        view.setup(RequestFactory().get("/", headers={"X-Count": "42"}))
        self.assertEqual(view.headers, HeaderView.Headers(x_count=42))
        self.assertIs(view.headers, view.headers)
        parameters = build_openapi(Route("", HeaderView))["paths"]["/"]["get"][
            "parameters"
        ]
        self.assertEqual(
            parameters[0],
            {
                "name": "x-count",
                "in": "header",
                "required": True,
                "schema": {"type": "integer", "minimum": 1},
            },
        )
        self.assertFalse(parameters[1]["required"])
        self.assertEqual(parameters[1]["schema"]["default"], 10)
        self.assertFalse(parameters[2]["required"])
        for headers in ({}, {"X-Count": "0"}):
            invalid = HeaderView()
            invalid.setup(RequestFactory().get("/", headers=headers))
            with self.assertRaises(msgspec.ValidationError):
                _ = invalid.headers

    def test_typed_dict_headers(self):
        class HeaderView(APIView):
            class Headers(TypedDict):
                x_count: int
                x_limit: NotRequired[int]

            def get(self, request):
                return {}

        for extra, expected in (({}, {}), ({"X-Limit": "5"}, {"x_limit": 5})):
            view = HeaderView()
            view.setup(RequestFactory().get("/", headers={"X-Count": "42", **extra}))
            self.assertEqual(view.headers, {"x_count": 42, **expected})
        parameters = build_openapi(Route("", HeaderView))["paths"]["/"]["get"][
            "parameters"
        ]
        self.assertTrue(parameters[0]["required"])
        self.assertFalse(parameters[1]["required"])
        self.assertEqual(parameters[1]["schema"], {"type": "integer"})
        view = HeaderView()
        view.setup(RequestFactory().get("/"))
        with self.assertRaises(msgspec.ValidationError):
            _ = view.headers

    def test_converts_headers_with_aliases_and_defaults(self):
        class HeaderView(APIView):
            class Headers(msgspec.Struct):
                x_count: int
                enabled: bool = msgspec.field(name="X-Enabled")
                x_empty: str
                request_id: uuid.UUID = msgspec.field(
                    name="X-Request-ID", default_factory=uuid.uuid4
                )
                x_optional: int | None = None
                x_missing: str | None = None
                x_limit: int = 10

        view = HeaderView()
        request_id = uuid.uuid4()
        view.setup(
            RequestFactory().get(
                "/",
                headers={
                    "X-Count": "42",
                    "x-enabled": "true",
                    "X-Request-ID": str(request_id),
                    "X-Optional": "7",
                    "X-Empty": "",
                    "X-Undeclared": "ignored",
                },
            )
        )
        self.assertIsInstance(view.headers, HeaderView.Headers)
        self.assertEqual(
            view.headers,
            HeaderView.Headers(
                x_count=42,
                enabled=True,
                request_id=request_id,
                x_empty="",
                x_optional=7,
                x_missing=None,
                x_limit=10,
            ),
        )

    def test_missing_required_header_raises_even_when_nullable(self):
        for annotation in (str, str | None):
            with self.subTest(annotation=annotation):
                header = msgspec.defstruct("Headers", [("x_required", annotation)])
                view = APIView(Headers=header)
                view.setup(RequestFactory().get("/"))
                with self.assertRaises(msgspec.ValidationError):
                    _ = view.headers

    def test_invalid_header_raises(self):
        class HeaderView(APIView):
            class Headers(msgspec.Struct):
                x_count: Annotated[int, msgspec.Meta(ge=1)]

        for value in ("invalid", "0"):
            with self.subTest(value=value):
                view = HeaderView()
                view.setup(RequestFactory().get("/", headers={"X-Count": value}))
                with self.assertRaises(msgspec.ValidationError):
                    _ = view.headers

    def test_headers_are_cached_per_view_and_inherited(self):
        class CountView(APIView):
            class Headers(APIView.Headers):
                x_count: int

        class ChildView(CountView):
            pass

        view = ChildView()
        view.setup(RequestFactory().get("/", headers={"X-Count": "1"}))
        headers = view.headers
        view.request.META["HTTP_X_COUNT"] = "2"
        del view.request.headers
        self.assertIs(view.headers, headers)
        self.assertEqual(view.headers.x_count, 1)

        other = ChildView()
        other.setup(RequestFactory().get("/", headers={"X-Count": "3"}))
        self.assertEqual(other.headers.x_count, 3)

    def test_default_factory(self):
        class HeaderView(APIView):
            class Headers(msgspec.Struct):
                x_values: list[str] = msgspec.field(default_factory=list)

        first, second = HeaderView(), HeaderView()
        for view in (first, second):
            view.setup(RequestFactory().get("/"))
        self.assertEqual(first.headers.x_values, [])
        self.assertIsNot(first.headers.x_values, second.headers.x_values)

    def test_no_declared_headers(self):
        view = APIView()
        view.setup(RequestFactory().get("/", headers={"X-Other": "value"}))
        self.assertEqual(view.headers, APIView.Headers())
