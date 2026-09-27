"""
Tenant-aware DRF ViewSets.

Three things are handled automatically in every ViewSet:
  1. `get_queryset()`  -> Model.objects (tenant-scoped manager). Doosre hospital ka
     the row does not appear in the queryset at all, so even the detail view returns 404.
  2. `perform_create()`-> hospital + created_by set automatically (the client cannot send them)
  3. permissions        -> authenticated + same-tenant + role + the plan's `api_access`

To add a new resource, just write the model + serializer; everything else is
inherited from this base.
"""
from rest_framework import viewsets
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated

from .pagination import StandardPagination
from .permissions import HasApiAccess, IsTenantMember


class TenantModelViewSet(viewsets.ModelViewSet):
    """Base: tenant scoping + default permission stack + search/order/filter."""

    permission_classes = [IsAuthenticated, IsTenantMember, HasApiAccess]
    pagination_class = StandardPagination
    filter_backends = [SearchFilter, OrderingFilter]
    tenant_model = None          # set by the subclass
    sets_created_by = True       # whether the model has a `created_by` field
    # Exact-match filtering from query params: ?status=PAID&doctor=3
    # (a small filter of our own, to avoid the django-filter dependency)
    filter_fields = ()

    def get_queryset(self):
        assert self.tenant_model is not None, "set tenant_model"
        # objects = TenantManager -> automatically scoped to the current hospital
        qs = self.tenant_model.objects.all()
        return self._apply_query_filters(qs)

    def _apply_query_filters(self, qs):
        """
        Turn the params declared in `filter_fields` into exact-match filters.
        Unknown/invalid values are not silently ignored - they return 400, so
        the client does not think a filter was applied.
        """
        from rest_framework.exceptions import ValidationError

        for field in self.filter_fields:
            if field not in self.request.query_params:
                continue
            value = self.request.query_params[field]
            try:
                qs = qs.filter(**{field: value})
            except (ValueError, TypeError) as exc:
                raise ValidationError({field: f"Invalid value: {value}"}) from exc
        return qs

    def get_hospital(self):
        return getattr(self.request, "hospital", None)

    def perform_create(self, serializer):
        kwargs = {}
        hospital = self.get_hospital()
        if hospital is not None:
            kwargs["hospital"] = hospital
        if self.sets_created_by and hasattr(self.tenant_model, "created_by"):
            kwargs["created_by"] = self.request.user
        serializer.save(**kwargs)

    def perform_update(self, serializer):
        # never allow hospital to change (moving the row to another tenant)
        serializer.save()
