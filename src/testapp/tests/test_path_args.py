from django.http import HttpRequest
from django.test import SimpleTestCase

from apiary import APIView, Route
from apiary.openapi import build_openapi


class PathArgsTests(SimpleTestCase):
    def test_positional_parameters_from_nested_routes(self):
        class Detail(APIView):
            # Handler order deliberately differs from URL order.
            def get(self, request: HttpRequest, item_id: int, account_id: int, /):
                return {"account": account_id, "item": item_id}

        route = Route("accounts/<int:account_id>/")(
            Route("items/<int:item_id>/", Detail)
        )
        with self.settings(ROOT_URLCONF=tuple(route.to_urlpatterns())):
            response = self.client.get(
                "/accounts/42/items/7/?account_id=99&item_id=100"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"account": 42, "item": 7})

        operation = build_openapi(route)["paths"][
            "/accounts/{account_id}/items/{item_id}/"
        ]["get"]
        self.assertEqual(
            operation["parameters"],
            [
                {
                    "name": name,
                    "in": "path",
                    "required": True,
                    "schema": {"type": "integer"},
                }
                for name in ("item_id", "account_id")
            ],
        )

    def test_catches_nested_path_parameters_in_url_order(self):
        class Detail(APIView):
            def get(self, request: HttpRequest, *args, limit: int = 10, **extras):
                return {"args": args, "limit": limit, "extras": extras}

        route = Route("accounts/<int:account_id>/")(Route("items/<slug:item>/", Detail))
        with self.settings(ROOT_URLCONF=tuple(route.to_urlpatterns())):
            response = self.client.get(
                "/accounts/42/items/example/?limit=5&other=value"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"args": [42, "example"], "limit": 5, "extras": {"other": "value"}},
        )
        operation = build_openapi(route)["paths"][
            "/accounts/{account_id}/items/{item}/"
        ]["get"]
        self.assertEqual(
            operation["parameters"],
            [
                {
                    "name": "limit",
                    "in": "query",
                    "required": False,
                    "schema": {"type": "integer", "default": 10},
                },
                {"name": "account_id", "in": "path", "required": True, "schema": {}},
                {"name": "item", "in": "path", "required": True, "schema": {}},
            ],
        )
        self.assertNotIn("requestBody", operation)

    def test_named_path_parameters_are_not_captured_again(self):
        class Detail(APIView):
            def get(
                self, request: HttpRequest, account_id: int, /, *args, item_id: int
            ):
                return {"account": account_id, "args": args, "item": item_id}

        route = Route("<int:account_id>/<slug:section>/<int:item_id>/", Detail)
        with self.settings(ROOT_URLCONF=tuple(route.to_urlpatterns())):
            response = self.client.get("/42/example/7/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {"account": 42, "args": ["example"], "item": 7}
        )
        parameters = build_openapi(route)["paths"][
            "/{account_id}/{section}/{item_id}/"
        ]["get"]["parameters"]
        self.assertEqual(
            {p["name"]: p["schema"] for p in parameters},
            {
                "account_id": {"type": "integer"},
                "item_id": {"type": "integer"},
                "section": {},
            },
        )
        self.assertEqual(len(parameters), 3)
        self.assertTrue(all(p["required"] and p["in"] == "path" for p in parameters))

    def test_parameters_before_args_keep_their_values_and_defaults(self):
        class Detail(APIView):
            def get(self, request: HttpRequest, limit: int = 10, *args):
                return {"limit": limit, "args": args}

        route = Route("<int:item_id>/", Detail)
        with self.settings(ROOT_URLCONF=tuple(route.to_urlpatterns())):
            for query, limit in (("", 10), ("?limit=5", 5)):
                with self.subTest(query=query):
                    response = self.client.get(f"/42/{query}")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), {"limit": limit, "args": [42]})

    def test_async_body_parameters_and_empty_args(self):
        class Detail(APIView):
            async def post(self, request: HttpRequest, *args, label: str):
                return {"args": args, "label": label}

        for path, url, args in (("", "/", []), ("<int:item_id>/", "/42/", [42])):
            with self.subTest(path=path):
                route = Route(path, Detail)
                with self.settings(ROOT_URLCONF=tuple(route.to_urlpatterns())):
                    response = self.client.post(url, {"label": "hello"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"args": args, "label": "hello"})
                operation = next(iter(build_openapi(route)["paths"].values()))["post"]
                self.assertEqual(
                    operation["requestBody"]["content"]["application/json"]["schema"],
                    {
                        "type": "object",
                        "properties": {"label": {"type": "string"}},
                        "required": ["label"],
                    },
                )
                self.assertEqual(len(operation.get("parameters", [])), len(args))

    def test_args_alone_accepts_an_empty_path(self):
        class Detail(APIView):
            def get(self, request: HttpRequest, *args):
                return args

        route = Route("", Detail)
        with self.settings(ROOT_URLCONF=tuple(route.to_urlpatterns())):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        operation = build_openapi(route)["paths"]["/"]["get"]
        self.assertNotIn("parameters", operation)
        self.assertNotIn("requestBody", operation)
