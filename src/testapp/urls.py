from apiary import OpenAPIView, Route
from apiary.middleware import RequireAuth, Throttle

from . import views

api = Route("api/v1/")(
    Route("auth/", views.AuthAPI),
    RequireAuth(
        Route("issue/<int:issue_id>/", views.IssueAPI, name="api-v1-issue"),
        Route("search/", views.SearchAPI)(
            Route("<int:search_id>/", views.SavedSearchAPI),
        ),
    ),
    Throttle("5/s", "1000/day")(
        Route("throttled/", views.ThrottledAPI),
    ),
)

urlpatterns = [
    api.to_urlpattern(),
    Route("openapi.json", OpenAPIView, route=api, title="Test API").to_urlpattern(),
]
