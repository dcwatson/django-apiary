from types import SimpleNamespace
from urllib.parse import quote_plus

from django.http import HttpRequest, QueryDict
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from django.utils.datastructures import MultiValueDict

from apiary import APIView
from apiary.utils import FastJsonResponse, build_args

from ..types import AdvancedSearch, Criteria, Operator
from ..views import IssueAPI, SearchAPI


def authenticated_user_middleware(get_response):
    def middleware(request):
        request.user = SimpleNamespace(is_authenticated=True)
        return get_response(request)

    return middleware


class ParamView(APIView):
    def get(self, request: HttpRequest, ids: list[int]):
        pass


class BasicTests(TestCase):
    def setUp(self) -> None:
        # Sort JSON keys for deterministic comparisons.
        self.client = Client(headers={"x-json-sorted": "true"})

    def test_build_args(self):
        now = timezone.now()
        now_qs = quote_plus(now.isoformat())
        self.assertEqual(
            build_args(
                SearchAPI().get,
                QueryDict(f"query=hello%20world&since={now_qs}"),
            ),
            ([], {"query": "hello world", "since": now}),
        )
        self.assertEqual(
            build_args(
                SearchAPI().post,
                MultiValueDict(
                    {
                        "criteria": [
                            {"field": "name", "operator": "=", "value": "dan"},
                            {"field": "age", "operator": ">", "value": "40"},
                        ]
                    }
                ),
            ),
            (
                [],
                {
                    "search": AdvancedSearch(
                        criteria=[
                            Criteria(
                                field="name", operator=Operator.EQUAL, value="dan"
                            ),
                            Criteria(
                                field="age", operator=Operator.GREATER, value="40"
                            ),
                        ],
                        limit=10,
                    )
                },
            ),
        )

        self.assertEqual(
            build_args(ParamView().get, QueryDict("ids=1&ids=2")),
            ([], {"ids": [1, 2]}),
        )

        self.assertEqual(
            build_args(
                IssueAPI().post,
                QueryDict("comment=testing&field1=1&field2=two"),
                issue_id=1,
            ),
            ([1], {"comment": "testing", "field1": "1", "field2": "two"}),
        )

    @override_settings(
        MIDDLEWARE=["testapp.tests.test_basic.authenticated_user_middleware"]
    )
    def test_api_calls(self):
        r = self.client.get("/api/v1/search/?query=hello")
        assert isinstance(r, FastJsonResponse)
        self.assertEqual(r.content, b'{"hits":[],"total":0}')

        r = self.client.post(
            "/api/v1/issue/42/",
            data={"comment": "hello there!", "field1": "value1"},
            content_type="application/json",
        )
        assert isinstance(r, FastJsonResponse)
        self.assertEqual(
            r.content,
            b'{"comments":["hello there!"],"fields":{"field1":"value1"},"id":42}',
        )
