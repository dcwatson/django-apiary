# django-apiary

Typed API views in Django.


## API Views

Apiary's `APIView` extends Django's generic `View`, with a few main differences:

1. All parameters must be type-annotated.
2. They automatically return JSON objects unless you return your own `HttpResponse`.
3. Parameter values are assigned from the URL path parameters if applicable, then from the querystring for GET requests, and either POST or a JSON body for non-GET requests.

```python
class AuthAPI(APIView):
    class Response(TypedDict):
        authenticated: bool
        token: NotRequired[str]

    def get(self, request: HttpRequest) -> Response:
        return self.Response(authenticated=False)

    def post(self, request: HttpRequest, username: str, password: str) -> Response:
        return self.Response(authenticated=True, token="authtoken")
```

The `post` method of this view could be called using form- or JSON-encoded data:

```
% curl -d 'username=admin&password=letmein' http://localhost:8000/api/v1/auth/

{"authenticated":true,"username":"admin","token":"authtoken"}

% curl -H 'Content-Type: application/json' -d '{"username":"admin","password":"letmein"}' \
    http://localhost:8000/api/v1/auth/

{"authenticated":true,"username":"admin","token":"authtoken"}
```


## Typed Headers

An `APIView` may define it's own inner `Headers` class as either a `msgspec.Struct`, dataclass, or `TypedDict`. Accessing `self.headers` will attempt to construct this type from `request.headers`, changing dashes to underscores in header names. Using `msgspec.Struct` allows you to specify the header name distinct from the attribute name in `Headers`.

```python
class HeaderView(APIView):
    class Headers(msgspec.Struct):
        x_count: int
        enabled: bool = msgspec.field(name="X-Enabled")
        request_id: uuid.UUID = msgspec.field(
            name="X-Request-ID", default_factory=uuid.uuid4
        )
        x_optional: int | None = None

    # This ensures the type-checker knows about our Headers and not the default APIView.Headers
    headers: Headers

    def get(self, request: HttpRequest):
        return self.headers
```


## Routing and middleware

Since `APIView` is a regular Django `View` subclass, you can use `path` or `re_path` like normal. But Apiary also comes with a lightweight middleware system that allows you to be a bit more declarative when defining your URL scheme. For instance:

```python
from apiary import Route
from apiary.middleware import RequireAuth, Throttle

api_root = Route("api/v1/")(
    # Throttles requests for all children to 120 per minute.
    Throttle("120/min")(
        # Children may be API endpoints.
        Route("auth/", views.AuthAPI),
        # Or middleware. RequireAuth checks `request.user.is_authenticated`.
        RequireAuth(
            Route("issue/<int:issue_id>/", views.IssueAPI, name="api-v1-issue"),
            Route("search/", views.SearchAPI)(
                Route("<int:search_id>/", views.SavedSearchAPI),
            ),
        ),
        # RequireAuth can also take one or more permissions to check.
        RequireAuth("myapp.admin_perm")(
            Route("admin/", views.AdminAPI),
        ),
    ),
)
```

You can also define your own `APIMiddleware` (or `Route`, which is just a middleware that also yields URL patterns):

```python
from apiary import exceptions
from .models import AccessToken


class AuthenticatedAPI(Route):
    def on_request(self, request: HttpRequest) -> HttpResponseBase | None:
        if not AccessToken.check_access(request):
            raise exceptions.AuthenticationFailed()

    def on_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase | None:
        pass


api_root = AuthenticatedAPI("/api")(...)
```


### RequireAuth

`RequireAuth` checks `request.user.is_authenticated` and raises `AuthenticationFailed` if the user is not authenticated. It may also accept a list of permissions to check; if the user does not have all the specified permissions, `PermissionDenied` is raised.


### Throttle

By default, throttles with equivalent rate windows and bucket settings share usage per IP. Use `scope` to give identical policies independent quotas, for example `Throttle("120/min", scope="search")` and `Throttle("120/min", scope="login")`. Different policies always use separate cache entries, even with the same scope.


## Regular (function) views

Apiary also provides an `api_path` function that can be dropped in for Django's `path`, and calls your function-based views with the same data sourcing and argument sematics as `APIView`:

```python
def search_view(request: HttpRequest, query: str = "", limit: int | None = None): ...


from apiary import api_path

urlpatterns = [
    api_path("search/", search_view, name="search"),
]
```


## OpenAPI

Apiary comes with an `OpenAPIView` that can render an OpenAPI specification for your API:

```python
from apiary import OpenAPIView, api_endpoint

api_root = Route("api/v1/")(...)

urlpatterns = [
    api_root.to_urlpattern(),
    api_endpoint("openapi.json", OpenAPIView, route=api_root, title="Test API"),
]
```
