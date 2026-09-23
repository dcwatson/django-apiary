import datetime
from enum import Enum
from typing import Annotated
from unittest.mock import patch
from uuid import UUID

import msgspec
from django.http import HttpRequest, HttpResponse
from django.test import SimpleTestCase

from apiary import APIView, OpenAPIView, Route
from apiary.openapi import build_openapi

from ..types import AdvancedSearch
from ..urls import api
from ..views import SearchAPI


class OpenAPITests(SimpleTestCase):
    def test_header_parameters_on_each_operation(self):
        class HeaderView(APIView):
            class Headers(msgspec.Struct):
                count: Annotated[int, msgspec.Meta(ge=1)] = msgspec.field(
                    name="X-Count"
                )
                required_nullable: str | None
                request_id: UUID | None = msgspec.field(
                    name="X-Request-ID", default=None
                )
                x_limit: int = 10
                x_values: list[str] = msgspec.field(default_factory=list)

        class Detail(HeaderView):
            def get(self, request: HttpRequest, item_id: int, limit: int = 10):
                return {}

            def post(self, request: HttpRequest, item_id: int, label: str):
                return {}

        operations = build_openapi(Route("<int:item_id>/", Detail))["paths"][
            "/{item_id}/"
        ]
        for method in ("get", "post"):
            parameters = operations[method]["parameters"]
            headers = {p["name"]: p for p in parameters if p["in"] == "header"}
            self.assertEqual(
                set(headers),
                {"X-Count", "required-nullable", "X-Request-ID", "x-limit", "x-values"},
            )
            self.assertTrue(headers["X-Count"]["required"])
            self.assertEqual(
                headers["X-Count"]["schema"], {"type": "integer", "minimum": 1}
            )
            self.assertEqual(
                headers["X-Request-ID"]["schema"],
                {
                    "anyOf": [{"type": "string", "format": "uuid"}, {"type": "null"}],
                    "default": None,
                },
            )
            self.assertTrue(headers["required-nullable"]["required"])
            self.assertEqual(
                headers["x-limit"]["schema"], {"type": "integer", "default": 10}
            )
            self.assertEqual(headers["x-values"]["schema"]["default"], [])
            for name in ("X-Request-ID", "x-limit", "x-values"):
                self.assertFalse(headers[name]["required"])
            self.assertIn(
                {
                    "name": "item_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "integer"},
                },
                parameters,
            )
        self.assertEqual(operations["get"]["parameters"][-1]["in"], "query")
        body = operations["post"]["requestBody"]["content"]["application/json"][
            "schema"
        ]
        self.assertEqual(set(body["properties"]), {"label"})

    def test_header_overrides_components_and_structured_queries(self):
        class Mode(Enum):
            FAST = "fast"
            SLOW = "slow"

        class Headers(msgspec.Struct):
            mode: Mode = msgspec.field(name="X-Mode")

        schema = build_openapi(Route("search/", SearchAPI, Headers=Headers))
        for method in ("get", "post"):
            parameters = schema["paths"]["/search/"][method]["parameters"]
            header = next(p for p in parameters if p["in"] == "header")
            self.assertEqual(header["name"], "X-Mode")
            self.assertTrue(header["required"])
            ref = header["schema"]["$ref"].rsplit("/", 1)[1]
            self.assertEqual(
                schema["components"]["schemas"][ref]["enum"], ["fast", "slow"]
            )

        class Structured(APIView):
            def get(self, request: HttpRequest, search: AdvancedSearch):
                return {}

        operation = build_openapi(Route("search/", Structured, Headers=Headers))[
            "paths"
        ]["/search/"]["get"]
        self.assertEqual(
            {(p["name"], p["in"]) for p in operation["parameters"]},
            {("X-Mode", "header"), ("criteria", "query"), ("limit", "query")},
        )

    def test_shared_types_keep_parameter_defaults_separate(self):
        class Counts(APIView):
            def get(
                self,
                request: HttpRequest,
                item_id: int,
                limit: int = 10,
                offset: int = 0,
            ) -> int:
                return 0

        operation = build_openapi(Route("<item_id>/", Counts))["paths"]["/{item_id}/"][
            "get"
        ]
        parameters = {p["name"]: p["schema"] for p in operation["parameters"]}
        self.assertEqual(
            parameters,
            {
                "item_id": {"type": "integer"},
                "limit": {"type": "integer", "default": 10},
                "offset": {"type": "integer", "default": 0},
            },
        )
        self.assertEqual(
            operation["responses"]["200"]["content"]["application/json"]["schema"],
            {"type": "integer"},
        )

    def test_fixed_arguments_do_not_need_json_schemas(self):
        class InternalContext:
            pass

        class Detail(APIView):
            def get(self, request: HttpRequest, context: InternalContext):
                return {}

        schema = build_openapi(
            Route("detail/", Detail, url_kwargs={"context": InternalContext()})
        )
        self.assertEqual(schema["components"]["schemas"], {})
        operation = schema["paths"]["/detail/"]["get"]
        self.assertNotIn("parameters", operation)
        self.assertNotIn("content", operation["responses"]["200"])

    def test_walks_middleware_without_generating_urlpatterns(self):
        with patch.object(Route, "urlpatterns", side_effect=AssertionError):
            schema = build_openapi(api)
        self.assertIn("/api/v1/search/{search_id}/", schema["paths"])

    def test_nested_paths_and_parameters(self):
        schema = build_openapi(api, title="Test API", version="2")
        self.assertEqual(schema["info"], {"title": "Test API", "version": "2"})
        paths = schema["paths"]
        self.assertEqual(
            set(paths),
            {
                "/api/v1/auth/",
                "/api/v1/issue/{issue_id}/",
                "/api/v1/search/",
                "/api/v1/search/{search_id}/",
            },
        )
        issue = paths["/api/v1/issue/{issue_id}/"]["post"]
        self.assertEqual(
            issue["parameters"],
            [
                {
                    "name": "issue_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "integer"},
                }
            ],
        )
        self.assertEqual(issue["operationId"], "api-v1-issue-post")
        body = issue["requestBody"]["content"]["application/json"]["schema"]
        self.assertEqual(set(body["properties"]), {"comment"})
        self.assertTrue(body["additionalProperties"])
        query = {p["name"]: p for p in paths["/api/v1/search/"]["get"]["parameters"]}
        self.assertTrue(query["query"]["required"])
        self.assertFalse(query["limit"]["required"])
        self.assertEqual(query["limit"]["schema"]["default"], 10)
        self.assertFalse(query["since"]["required"])

    def test_components_and_structured_body(self):
        schema = build_openapi(api)
        post = schema["paths"]["/api/v1/search/"]["post"]
        body = post["requestBody"]["content"]["application/json"]["schema"]
        self.assertEqual(body, {"$ref": "#/components/schemas/AdvancedSearch"})
        self.assertEqual(
            schema["components"]["schemas"]["AdvancedSearch"]["required"], ["criteria"]
        )

        def check_refs(value):
            if isinstance(value, dict):
                if "$ref" in value:
                    self.assertTrue(value["$ref"].startswith("#/components/schemas/"))
                    self.assertIn(
                        value["$ref"].rsplit("/", 1)[1], schema["components"]["schemas"]
                    )
                for child in value.values():
                    check_refs(child)
            elif isinstance(value, list):
                for child in value:
                    check_refs(child)

        check_refs(schema)
        response = post["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertIn("$ref", response)

    def test_schema_view_always_returns_json(self):
        response = self.client.get("/openapi.json", headers={"Accept": "text/html"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        # self.assertEqual(response.json(), build_openapi(api, title="Test API"))
        self.assertEqual(self.client.post("/openapi.json").status_code, 405)

    def test_empty_route_and_schema_endpoint_exclusion(self):
        root = Route("api/")
        root(Route("openapi.json", OpenAPIView, route=root))
        self.assertEqual(build_openapi(root)["paths"], {})
        root(Route("search/", SearchAPI))
        self.assertEqual(set(build_openapi(root)["paths"]), {"/api/search/"})

    def test_converter_in_parent_and_untyped_response(self):
        class Detail(APIView):
            def get(self, request: HttpRequest, account_id: int):
                return {}

        schema = build_openapi(
            Route("accounts/<int:account_id>/")(Route("detail/", Detail))
        )
        operation = schema["paths"]["/accounts/{account_id}/detail/"]["get"]
        self.assertEqual(operation["parameters"][0]["schema"], {"type": "integer"})

    def test_path_schemas_use_handler_annotations(self):
        class Events(APIView):
            def get(
                self,
                request: HttpRequest,
                day: datetime.date,
                timestamp: Annotated[datetime.datetime, msgspec.Meta(tz=True)],
                event_id: UUID,
                count: Annotated[int, msgspec.Meta(ge=1)] = 1,
            ):
                return {}

        schema = build_openapi(
            Route("events/<day>/")(Route("<str:timestamp>/<event_id>/<count>/", Events))
        )
        operation = schema["paths"]["/events/{day}/{timestamp}/{event_id}/{count}/"][
            "get"
        ]
        parameters = {value["name"]: value for value in operation["parameters"]}
        for name, fmt in (
            ("day", "date"),
            ("timestamp", "date-time"),
            ("event_id", "uuid"),
        ):
            self.assertEqual(
                parameters[name]["schema"], {"type": "string", "format": fmt}
            )
        self.assertEqual(
            parameters["count"]["schema"], {"type": "integer", "minimum": 1}
        )
        self.assertTrue(all(value["required"] for value in parameters.values()))
        self.assertTrue(all(value["in"] == "path" for value in parameters.values()))

    def test_path_parameters_require_handler_annotations(self):
        class Untyped(APIView):
            def get(self, request: HttpRequest, item_id):
                return {}

        with self.assertRaisesRegex(ValueError, "type-annotated"):
            build_openapi(Route("<int:item_id>/", Untyped))

        class Missing(APIView):
            def get(self, request: HttpRequest):
                return {}

        with self.assertRaisesRegex(ValueError, "declared on the handler: item_id"):
            build_openapi(Route("<int:item_id>/", Missing))

    def test_response_objects_are_not_inferred_as_json(self):
        class Download(APIView):
            def get(self, request: HttpRequest) -> HttpResponse:
                return HttpResponse("hello")

        response = build_openapi(Route("download/", Download))["paths"]["/download/"][
            "get"
        ]["responses"]["200"]
        self.assertNotIn("content", response)

    def test_async_structured_query_and_view_kwargs(self):
        class Search(APIView):
            http_method_names = ()

            async def get(self, request: HttpRequest, search: AdvancedSearch) -> dict:
                return {}

        schema = build_openapi(Route("search/", Search, http_method_names=["get"]))
        operation = schema["paths"]["/search/"]["get"]
        parameters = {p["name"]: p for p in operation["parameters"]}
        self.assertEqual(set(parameters), {"criteria", "limit"})
        self.assertTrue(parameters["criteria"]["required"])
        self.assertFalse(parameters["limit"]["required"])
        self.assertEqual(parameters["limit"]["schema"]["default"], 10)
        self.assertNotIn("requestBody", operation)

    def test_path_body_and_fixed_inputs(self):
        class Detail(APIView):
            def post(
                self,
                request: HttpRequest,
                item_id: int,
                /,
                fixed: str,
                label: str = "untitled",
                **extras,
            ) -> list[str] | None:
                return None

        route = Route("items/<int:item_id>/", Detail, url_kwargs={"fixed": "value"})
        operation = build_openapi(route)["paths"]["/items/{item_id}/"]["post"]
        self.assertEqual([p["name"] for p in operation["parameters"]], ["item_id"])
        body = operation["requestBody"]
        self.assertFalse(body["required"])
        self.assertEqual(
            body["content"]["application/json"]["schema"],
            {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "default": "untitled"},
                },
                "additionalProperties": True,
            },
        )
        response = operation["responses"]["200"]["content"]["application/json"][
            "schema"
        ]
        self.assertIn("anyOf", response)
        self.assertEqual(build_openapi(route), build_openapi(route))

    def test_classifies_structured_inputs_by_method(self):
        operations = build_openapi(Route("search/", SearchAPI))["paths"]["/search/"]
        self.assertTrue(
            all(value["in"] == "query" for value in operations["get"]["parameters"])
        )
        self.assertNotIn("requestBody", operations["get"])
        self.assertNotIn("parameters", operations["post"])
        self.assertEqual(
            operations["post"]["requestBody"]["content"]["application/json"]["schema"],
            {"$ref": "#/components/schemas/AdvancedSearch"},
        )
