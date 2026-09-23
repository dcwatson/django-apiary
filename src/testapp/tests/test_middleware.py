from types import SimpleNamespace
from unittest.mock import Mock

import msgspec
from django.test import RequestFactory, SimpleTestCase
from django.urls import resolve
from django.utils.functional import SimpleLazyObject

from apiary import Route
from apiary.middleware import RequireAuth

from ..views import IssueAPI


class RequireAuthTests(SimpleTestCase):
    def test_protected_route_checks_authentication(self):
        for user, status in (
            (None, 401),
            (SimpleNamespace(is_authenticated=False), 401),
            (SimpleNamespace(is_authenticated=True), 200),
            (SimpleLazyObject(lambda: SimpleNamespace(is_authenticated=True)), 200),
        ):
            with self.subTest(user=user):
                request = RequestFactory().get("/api/v1/issue/42/")
                request.user = user
                match = resolve(request.path)
                response = match.func(request, **match.kwargs)
                self.assertEqual(response.status_code, status)
                if status == 401:
                    self.assertEqual(
                        msgspec.json.decode(response.content),
                        {"error": "Authentication failed."},
                    )
                else:
                    self.assertEqual(msgspec.json.decode(response.content)["id"], 42)

    def test_missing_user_is_rejected(self):
        response = self.client.get("/api/v1/issue/42/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Authentication failed."})

    def test_public_route_remains_accessible(self):
        response = self.client.get("/api/v1/auth/")
        self.assertEqual(response.status_code, 200)

    def test_all_permissions_are_required(self):
        for granted in (set(), {"perm1"}, {"perm2"}, {"perm1", "perm2"}):
            for deferred_children in (False, True):
                with self.subTest(granted=granted, deferred_children=deferred_children):
                    route = Route("issue/<int:issue_id>/", IssueAPI)
                    if deferred_children:
                        auth = RequireAuth("perm1", "perm2")(route)
                    else:
                        auth = RequireAuth("perm1", route, "perm2")
                    request = RequestFactory().get("/issue/42/")
                    has_perms = Mock(
                        side_effect=lambda perms, granted=granted: set(perms) <= granted
                    )
                    request.user = SimpleNamespace(
                        is_authenticated=True, has_perms=has_perms
                    )
                    match = resolve(request.path, urlconf=tuple(auth.to_urlpatterns()))
                    response = match.func(request, **match.kwargs)

                    has_perms.assert_called_once_with(("perm1", "perm2"))
                    if granted == {"perm1", "perm2"}:
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(
                            msgspec.json.decode(response.content)["id"], 42
                        )
                    else:
                        self.assertEqual(response.status_code, 403)
                        self.assertEqual(
                            msgspec.json.decode(response.content),
                            {"error": "Permission denied."},
                        )

    def test_permissions_are_not_checked_for_unauthenticated_users(self):
        has_perms = Mock(return_value=True)
        request = RequestFactory().get("/issue/42/")
        request.user = SimpleNamespace(is_authenticated=False, has_perms=has_perms)
        auth = RequireAuth("perm1", "perm2")(Route("issue/<int:issue_id>/", IssueAPI))
        match = resolve(request.path, urlconf=tuple(auth.to_urlpatterns()))
        response = match.func(request, **match.kwargs)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            msgspec.json.decode(response.content), {"error": "Authentication failed."}
        )
        has_perms.assert_not_called()

    def test_no_permissions_only_requires_authentication(self):
        request = RequestFactory().get("/issue/42/")
        has_perms = Mock(return_value=False)
        request.user = SimpleNamespace(is_authenticated=True, has_perms=has_perms)
        auth = RequireAuth(Route("issue/<int:issue_id>/", IssueAPI))
        match = resolve(request.path, urlconf=tuple(auth.to_urlpatterns()))
        response = match.func(request, **match.kwargs)

        self.assertEqual(response.status_code, 200)
        has_perms.assert_not_called()
