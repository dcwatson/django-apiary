from typing import Annotated

import msgspec
from django.http import HttpRequest, JsonResponse, QueryDict
from django.test import SimpleTestCase

from apiary import Alias, APIView, Route, api_path
from apiary.openapi import build_openapi
from apiary.utils import build_args


class AliasTests(SimpleTestCase):
    def test_query_alias_defaults_collections_and_extra_data(self):
        def handler(
            request,
            offset: Annotated[int, Alias("o")] = 0,
            *,
            ids: Annotated[list[int], Alias("id")] = (),
            **extras,
        ):
            return offset, ids, extras

        args, kwargs = build_args(
            handler, QueryDict("o=4&id=1&id=2&other=yes&offset=99")
        )
        self.assertEqual(handler(None, *args, **kwargs), (4, [1, 2], {"other": "yes"}))
        args, kwargs = build_args(handler, QueryDict("offset=5"))
        self.assertEqual(handler(None, *args, **kwargs), (0, (), {}))

    def test_required_alias_and_validation_metadata(self):
        def handler(request, offset: Annotated[int, Alias("o"), msgspec.Meta(ge=0)]):
            return offset

        with self.assertRaisesRegex(ValueError, "Value missing"):
            build_args(handler, QueryDict("offset=5"))
        with self.assertRaises(msgspec.ValidationError):
            build_args(handler, QueryDict("o=-1"))
        with self.assertRaises(msgspec.ValidationError):
            build_args(handler, QueryDict("o=invalid"))

    def test_function_view(self):
        def handler(request, offset: Annotated[int, Alias("o")] = 0):
            return JsonResponse({"offset": offset})

        with self.settings(ROOT_URLCONF=(api_path("", handler),)):
            self.assertEqual(self.client.get("/?o=7").json(), {"offset": 7})

    def test_body_alias_async_and_openapi(self):
        class Detail(APIView):
            async def post(
                self, request: HttpRequest, offset: Annotated[int, Alias("o")]
            ):
                return {"offset": offset}

        route = Route("", Detail)
        with self.settings(ROOT_URLCONF=tuple(route.to_urlpatterns())):
            for content_type in (
                "application/json",
                "application/x-www-form-urlencoded",
            ):
                with self.subTest(content_type=content_type):
                    data = {"o": 3} if content_type == "application/json" else "o=3"
                    response = self.client.post("/", data, content_type=content_type)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), {"offset": 3})
        schema = build_openapi(route)["paths"]["/"]["post"]["requestBody"]["content"][
            "application/json"
        ]["schema"]
        self.assertEqual(
            schema,
            {
                "type": "object",
                "properties": {"o": {"type": "integer"}},
                "required": ["o"],
            },
        )

    def test_path_aliases_with_args_and_openapi(self):
        class Detail(APIView):
            def get(
                self,
                request: HttpRequest,
                item_id: Annotated[int, Alias("id")],
                /,
                *args,
                offset: Annotated[int, Alias("o")] = 0,
            ):
                return {"item": item_id, "args": args, "offset": offset}

        route = Route("<int:id>/<slug:section>/", Detail)
        with self.settings(ROOT_URLCONF=tuple(route.to_urlpatterns())):
            response = self.client.get("/42/example/?o=5")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {"item": 42, "args": ["example"], "offset": 5}
        )
        params = build_openapi(route)["paths"]["/{id}/{section}/"]["get"]["parameters"]
        self.assertEqual(
            params[:2],
            [
                {
                    "name": "id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "integer"},
                },
                {
                    "name": "o",
                    "in": "query",
                    "required": False,
                    "schema": {"type": "integer", "default": 0},
                },
            ],
        )
