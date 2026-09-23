from apiary import OpenAPIView, Route
from apiary.middleware import RequireAuth, Throttle

from . import views

api = Route("api/v1/")(
    Throttle("100/min")(
        Route("auth/", views.AuthAPI),
        RequireAuth(
            Route("issue/<int:issue_id>/", views.IssueAPI, name="api-v1-issue"),
            Route("search/", views.SearchAPI)(
                Route("<int:search_id>/", views.SavedSearchAPI),
            ),
        ),
    ),
)

urlpatterns = [
    api.to_urlpattern(),
    Route("openapi.json", OpenAPIView, route=api, title="Test API").to_urlpattern(),
]
