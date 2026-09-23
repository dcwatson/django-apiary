import datetime
from typing import NotRequired, TypedDict

import msgspec
from django.http import HttpRequest

from apiary import APIView

from .types import AdvancedSearch, Sort


class AuthAPI(APIView):
    class Response(TypedDict):
        authenticated: bool
        username: NotRequired[str]
        token: NotRequired[str]

    def get(self, request: HttpRequest) -> Response:
        return self.Response(authenticated=False)

    def post(self, request: HttpRequest, username: str, password: str) -> Response:
        return self.Response(authenticated=True, username=username, token="authtoken")


class IssueAPI(APIView):
    class Response(msgspec.Struct):
        id: int
        fields: dict[str, str] = {}
        comments: list[str] = []

    def get(self, request: HttpRequest, issue_id: int):
        return self.Response(id=issue_id)

    def post(
        self,
        request: HttpRequest,
        issue_id: int,
        /,
        comment: str | None = None,
        **updates,
    ):
        return self.Response(
            id=issue_id,
            fields=updates,
            comments=[comment] if comment else [],
        )


class SearchAPI(APIView):
    class Response(msgspec.Struct):
        total: int
        hits: list[int] = []

    def get(
        self,
        request: HttpRequest,
        /,
        query: str,
        limit: int = 10,
        offset: int = 0,
        sort: Sort = Sort.RANK,
        since: datetime.datetime | None = None,
        **kwargs,
    ) -> Response:
        return self.Response(total=0)

    def post(self, request: HttpRequest, search: AdvancedSearch) -> Response:
        return self.Response(total=0)


class SavedSearchAPI(APIView):
    def get(self, request: HttpRequest, search_id: int):
        return {}
