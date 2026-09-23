from django.http import HttpRequest, JsonResponse
from django.test import SimpleTestCase
from django.urls import include, path, re_path, reverse

from apiary import APIView, api_endpoint, api_path


class RoutingTests(SimpleTestCase):
    def test_as_view_with_path_supports_configuration_and_url_names(self):
        class Detail(APIView):
            label = "default"

            def get(
                self,
                request: HttpRequest,
                item_id: int,
                /,
                source: str,
                limit: int = 10,
            ):
                return {
                    "item": item_id,
                    "source": source,
                    "limit": limit,
                    "label": self.label,
                }

        patterns = (
            path(
                "items/<int:item_id>/",
                Detail.as_view(label="configured"),
                kwargs={"source": "internal"},
                name="item-detail",
            ),
        )
        with self.settings(ROOT_URLCONF=patterns):
            url = reverse("item-detail", kwargs={"item_id": 7})
            self.assertEqual(url, "/items/7/")
            response = self.client.get(f"{url}?item_id=99&source=external&limit=5")
            self.assertEqual(self.client.post(url).status_code, 405)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"item": 7, "source": "internal", "limit": 5, "label": "configured"},
        )

    def test_as_view_with_nested_paths_parses_async_json_body(self):
        class Detail(APIView):
            async def post(
                self, request: HttpRequest, item_id: int, account_id: int, /, count: int
            ):
                return {"account": account_id, "item": item_id, "count": count}

        patterns = (
            path(
                "accounts/<int:account_id>/",
                include([path("items/<int:item_id>/", Detail.as_view())]),
            ),
        )
        with self.settings(ROOT_URLCONF=patterns):
            response = self.client.post(
                "/accounts/42/items/7/",
                {"account_id": 99, "item_id": 100, "count": "3"},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"account": 42, "item": 7, "count": 3})

    def test_as_view_with_re_path_binds_named_capture(self):
        class Detail(APIView):
            # Unlike path converters, regex captures are passed as strings.
            def get(self, request: HttpRequest, item_id: str, /, limit: int = 10):
                return {"item": item_id, "limit": limit}

        patterns = (
            re_path(
                r"^items/(?P<item_id>[0-9]+)/$",
                Detail.as_view(),
                name="item-detail",
            ),
        )
        with self.settings(ROOT_URLCONF=patterns):
            url = reverse("item-detail", kwargs={"item_id": "007"})
            self.assertEqual(url, "/items/007/")
            response = self.client.get(f"{url}?item_id=99&limit=5")
            self.assertEqual(self.client.get("/items/example/").status_code, 404)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"item": "007", "limit": 5})

    def test_api_path_converts_query_parameters_and_preserves_response(self):
        def search(request: HttpRequest, ids: list[int], limit: int = 10):
            return JsonResponse(
                {"ids": ids, "limit": limit},
                status=202,
                headers={"X-Result": "search"},
            )

        with self.settings(ROOT_URLCONF=(api_path("search/", search),)):
            for query, limit in (("", 10), ("&limit=5", 5)):
                with self.subTest(query=query):
                    response = self.client.get(f"/search/?ids=1&ids=2{query}")
                    self.assertEqual(response.status_code, 202)
                    self.assertEqual(response["X-Result"], "search")
                    self.assertEqual(response.json(), {"ids": [1, 2], "limit": limit})

    def test_api_path_parses_form_and_json_bodies(self):
        def create(request: HttpRequest, count: int, label: str, **extras):
            return JsonResponse({"count": count, "label": label, "extras": extras})

        with self.settings(ROOT_URLCONF=(api_path("items/", create),)):
            for options in ({}, {"content_type": "application/json"}):
                with self.subTest(options=options):
                    response = self.client.post(
                        "/items/?label=hello",
                        {"count": "3", "extra": "value"},
                        **options,
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(
                        response.json(),
                        {"count": 3, "label": "hello", "extras": {"extra": "value"}},
                    )

    def test_api_path_binds_nested_url_parameters(self):
        def detail(request: HttpRequest, item_id: int, account_id: int, /):
            return JsonResponse({"account": account_id, "item": item_id})

        patterns = (
            path(
                "accounts/<int:account_id>/",
                include([api_path("items/<int:item_id>/", detail)]),
            ),
        )
        with self.settings(ROOT_URLCONF=patterns):
            response = self.client.get("/accounts/42/items/7/?account_id=99")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"account": 42, "item": 7})

    def test_api_path_passes_remaining_url_parameters_to_args(self):
        def detail(request: HttpRequest, *args, limit: int = 10):
            return JsonResponse({"args": args, "limit": limit})

        patterns = (api_path("<int:account_id>/<slug:item>/", detail),)
        with self.settings(ROOT_URLCONF=patterns):
            response = self.client.get("/42/example/?limit=5")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"args": [42, "example"], "limit": 5})

    def test_api_path_supports_fixed_kwargs_and_url_names(self):
        def detail(request: HttpRequest, item_id: int, /, source: str):
            return JsonResponse({"item": item_id, "source": source})

        patterns = (
            api_path(
                "items/<int:item_id>/",
                detail,
                kwargs={"source": "internal"},
                name="item-detail",
            ),
        )
        with self.settings(ROOT_URLCONF=patterns):
            url = reverse("item-detail", kwargs={"item_id": 7})
            self.assertEqual(url, "/items/7/")
            response = self.client.get(f"{url}?source=external")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"item": 7, "source": "internal"})

    def test_api_endpoint_configures_and_dispatches_view(self):
        class Detail(APIView):
            label = "default"

            def get(self, request: HttpRequest, item_id: int, /, source: str):
                return {"item": item_id, "source": source, "label": self.label}

        patterns = (
            api_endpoint(
                "items/<int:item_id>/",
                Detail,
                url_kwargs={"source": "internal"},
                name="item-detail",
                label="configured",
            ),
        )
        with self.settings(ROOT_URLCONF=patterns):
            url = reverse("item-detail", kwargs={"item_id": 7})
            self.assertEqual(url, "/items/7/")
            response = self.client.get(url)
            self.assertEqual(self.client.post(url).status_code, 405)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"item": 7, "source": "internal", "label": "configured"},
        )
