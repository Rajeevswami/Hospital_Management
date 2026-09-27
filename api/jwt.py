"""
Tenant-aware JWT.

SimpleJWT's default serializer only adds user_id/token_type. We add two more
claims:

    hospital  : which hospital the token belongs to (slug)
    role      : the user's role

This is CONVENIENCE only (so the client does not need an extra /api/me/ call).
The real authorization decision happens on the server:
  * JWTAuthentication loads the user via the tenant-scoped manager, so a
    hospital A token simply does not work on hospital B's subdomain
  * permissions (IsTenantMember + role + plan) are re-checked on every request

The token endpoint is itself tenant-scoped: login goes through
`TenantModelBackend`, which authenticates only the current hospital's users.
"""
from django.utils import timezone
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView


class TenantTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["username"] = user.username
        token["role"] = user.role
        token["hospital"] = user.hospital.slug if user.hospital_id else None
        token["is_platform_admin"] = bool(user.is_platform_admin)
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        user = self.user
        data["user"] = {
            "id": user.pk,
            "username": user.username,
            "full_name": user.get_full_name(),
            "role": user.role,
            "hospital": user.hospital.slug if user.hospital_id else None,
            "hospital_name": user.hospital.name if user.hospital_id else None,
        }
        data["server_time"] = timezone.now().isoformat()
        return data


class TenantTokenObtainPairView(TokenObtainPairView):
    serializer_class = TenantTokenObtainPairSerializer
