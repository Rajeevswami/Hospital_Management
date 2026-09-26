"""
Tenant-aware JWT.

SimpleJWT ka default serializer sirf user_id/token_type dalta hai. Hum do claims
aur add karte hain:

    hospital  : kis hospital ka token hai (slug)
    role      : user ka role

Yeh sirf CONVENIENCE hai (client ko extra /api/me/ call na karna pade).
Authorization ka asli faisla server pe hi hota hai:
  * JWTAuthentication user ko tenant-scoped manager se load karta hai, isliye
    hospital A ka token hospital B ke subdomain pe kaam hi nahi karta
  * permissions (IsTenantMember + role + plan) har request pe dobara check hote hain

Token endpoint khud tenant-scoped hai: login `TenantModelBackend` se hota hai,
jo sirf current hospital ke users ko authenticate karta hai.
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
