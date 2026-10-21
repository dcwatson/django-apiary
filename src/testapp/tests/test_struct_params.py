from dataclasses import dataclass
from typing import Annotated, TypedDict

import msgspec
from django.test import SimpleTestCase

from apiary import Alias, APIView, Route


class Filters(msgspec.Struct):
    query: str
    limit: int = 10


class StructParamTests(SimpleTestCase):
    def test_rejects_struct_with_other_query_parameters(self):
        @dataclass
        class DataFilters:
            query: str

        class DictFilters(TypedDict):
            query: str

        for annotation in (
            Filters,
            Filters | None,
            Annotated[Filters, Alias("q")],
            DataFilters,
            DictFilters,
        ):
            for method in ("get", "head"):
                with self.subTest(annotation=annotation, method=method):

                    def handler(self, request, filters: annotation, offset: int = 0):
                        pass

                    view = type("Search", (APIView,), {method: handler})
                    with self.assertRaisesRegex(
                        TypeError,
                        "single structured parameter",
                    ):
                        Route("", view).to_urlpatterns()

    def test_rejects_multiple_structs_and_variadic_parameters(self):
        class Multiple(APIView):
            def get(self, request, first: Filters, second: Filters):
                pass

        class Extras(APIView):
            def get(self, request, filters: Filters, **extras):
                pass

        class Path(APIView):
            def get(self, request, item_id: int, filters: Filters):
                pass

        for view, path in ((Multiple, ""), (Extras, ""), (Path, "<int:item_id>/")):
            with (
                self.subTest(view=view),
                self.assertRaisesRegex(TypeError, "single structured parameter"),
            ):
                Route(path, view).to_urlpatterns()

    def test_single_struct_is_unpacked(self):
        class Query(msgspec.Struct):
            query: str
            ids: list[int] = msgspec.field(default_factory=list, name="id")

        class Search(APIView):
            def get(self, request, filters: Query):
                return filters

        with self.settings(ROOT_URLCONF=tuple(Route("", Search).to_urlpatterns())):
            response = self.client.get("/?query=hello&id=1&id=3")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"query": "hello", "id": [1, 3]})

    def test_post_can_mix_struct_and_scalar_parameters(self):
        class Search(APIView):
            def post(self, request, filters: Filters, offset: int):
                return {"filters": filters, "offset": offset}

        with self.settings(ROOT_URLCONF=tuple(Route("", Search).to_urlpatterns())):
            response = self.client.post(
                "/",
                {"filters": {"query": "hello"}, "offset": 2},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {"filters": {"query": "hello", "limit": 10}, "offset": 2}
        )
