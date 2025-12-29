"""Tests for authentication endpoints."""

import pytest
from httpx import AsyncClient


class TestRegister:
    """Tests for POST /api/v1/auth/register."""

    async def test_register_success(self, client: AsyncClient, test_user_data: dict):
        """Test successful user registration."""
        response = await client.post("/api/v1/auth/register", json=test_user_data)

        assert response.status_code == 201
        data = response.json()
        assert data["email"] == test_user_data["email"].lower()
        assert data["is_active"] is True
        # In test mode, users are auto-verified to skip email verification flow
        assert data["is_verified"] is True
        assert "id" in data
        assert "profile" in data
        assert data["profile"]["first_name"] == test_user_data["first_name"]
        assert data["profile"]["roles"] == ["angler"]

    async def test_register_duplicate_email(
        self, client: AsyncClient, registered_user: dict, test_user_data: dict
    ):
        """Test registration with existing email fails."""
        response = await client.post("/api/v1/auth/register", json=test_user_data)

        assert response.status_code == 409
        assert "already registered" in response.json()["detail"].lower()

    async def test_register_invalid_email(self, client: AsyncClient):
        """Test registration with invalid email fails."""
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "invalid-email",
                "password": "SecurePass123",
                "first_name": "Test",
                "last_name": "User",
            },
        )

        assert response.status_code == 422

    async def test_register_weak_password(self, client: AsyncClient):
        """Test registration with weak password fails."""
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "test@example.com",
                "password": "weak",  # Too short, no uppercase, no digit
                "first_name": "Test",
                "last_name": "User",
            },
        )

        assert response.status_code == 422

    async def test_register_password_no_uppercase(self, client: AsyncClient):
        """Test registration fails without uppercase letter."""
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "test@example.com",
                "password": "password123",  # No uppercase
                "first_name": "Test",
                "last_name": "User",
            },
        )

        assert response.status_code == 422


class TestLogin:
    """Tests for POST /api/v1/auth/login."""

    async def test_login_success(
        self, client: AsyncClient, registered_user: dict, test_user_data: dict
    ):
        """Test successful login returns tokens."""
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "email": test_user_data["email"],
                "password": test_user_data["password"],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
        assert "expires_in" in data

    async def test_login_wrong_password(
        self, client: AsyncClient, registered_user: dict
    ):
        """Test login with wrong password fails."""
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "email": registered_user["email"],
                "password": "WrongPassword123",
            },
        )

        assert response.status_code == 401
        assert "incorrect" in response.json()["detail"].lower()

    async def test_login_nonexistent_user(self, client: AsyncClient):
        """Test login with nonexistent email fails."""
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "nonexistent@example.com",
                "password": "SomePassword123",
            },
        )

        assert response.status_code == 401

    async def test_login_updates_last_login(
        self, client: AsyncClient, registered_user: dict, test_user_data: dict
    ):
        """Test that login updates last_login timestamp."""
        # First login
        await client.post(
            "/api/v1/auth/login",
            json={
                "email": test_user_data["email"],
                "password": test_user_data["password"],
            },
        )

        # The user should now have last_login set
        # We verify this by getting /me after login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={
                "email": test_user_data["email"],
                "password": test_user_data["password"],
            },
        )
        token = login_response.json()["access_token"]

        me_response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_response.json()["last_login"] is not None


class TestMe:
    """Tests for GET /api/v1/auth/me."""

    async def test_me_success(
        self, client: AsyncClient, auth_headers: dict, registered_user: dict
    ):
        """Test getting current user info."""
        response = await client.get("/api/v1/auth/me", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["email"] == registered_user["email"]
        assert "profile" in data

    async def test_me_no_token(self, client: AsyncClient):
        """Test /me without token fails."""
        response = await client.get("/api/v1/auth/me")

        assert response.status_code == 401

    async def test_me_invalid_token(self, client: AsyncClient):
        """Test /me with invalid token fails."""
        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer invalid_token"},
        )

        assert response.status_code == 401


class TestRefresh:
    """Tests for POST /api/v1/auth/refresh."""

    async def test_refresh_success(self, client: AsyncClient, auth_tokens: dict):
        """Test refreshing tokens returns new tokens."""
        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": auth_tokens["refresh_token"]},
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        # New tokens should be different (token rotation)
        assert data["access_token"] != auth_tokens["access_token"]
        assert data["refresh_token"] != auth_tokens["refresh_token"]

    async def test_refresh_with_access_token_fails(
        self, client: AsyncClient, auth_tokens: dict
    ):
        """Test using access token for refresh fails."""
        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": auth_tokens["access_token"]},
        )

        assert response.status_code == 401
        assert "invalid token type" in response.json()["detail"].lower()

    async def test_refresh_token_rotation_invalidates_old(
        self, client: AsyncClient, auth_tokens: dict
    ):
        """Test that refreshing invalidates the old refresh token."""
        # First refresh
        response1 = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": auth_tokens["refresh_token"]},
        )
        assert response1.status_code == 200

        # Try to use old refresh token again (should fail - token rotation)
        response2 = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": auth_tokens["refresh_token"]},
        )
        assert response2.status_code == 401
        assert "revoked" in response2.json()["detail"].lower()

    async def test_refresh_invalid_token(self, client: AsyncClient):
        """Test refresh with invalid token fails."""
        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "invalid_token"},
        )

        assert response.status_code == 401


class TestLogout:
    """Tests for POST /api/v1/auth/logout."""

    async def test_logout_success(self, client: AsyncClient, auth_headers: dict):
        """Test successful logout."""
        response = await client.post("/api/v1/auth/logout", headers=auth_headers)

        assert response.status_code == 200
        assert "logged out" in response.json()["message"].lower()

    async def test_logout_invalidates_token(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test that logout invalidates the access token."""
        # Logout
        await client.post("/api/v1/auth/logout", headers=auth_headers)

        # Try to use the same token for /me (should fail)
        response = await client.get("/api/v1/auth/me", headers=auth_headers)
        assert response.status_code == 401
        assert "revoked" in response.json()["detail"].lower()

    async def test_logout_no_token(self, client: AsyncClient):
        """Test logout without token fails."""
        response = await client.post("/api/v1/auth/logout")

        assert response.status_code == 401


class TestPasswordReset:
    """Tests for password reset endpoints."""

    async def test_forgot_password_success(
        self, client: AsyncClient, registered_user: dict
    ):
        """Test forgot password returns success message."""
        response = await client.post(
            "/api/v1/auth/forgot-password",
            json={"email": registered_user["email"]},
        )

        assert response.status_code == 200
        data = response.json()
        assert "password reset" in data["message"].lower()
        # In dev mode, token is returned
        assert data["details"] is not None
        assert "reset_token" in data["details"]

    async def test_forgot_password_nonexistent_email(self, client: AsyncClient):
        """Test forgot password with nonexistent email still returns success (enumeration prevention)."""
        response = await client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "nonexistent@example.com"},
        )

        assert response.status_code == 200
        data = response.json()
        # Same message for security
        assert "password reset" in data["message"].lower()
        # But no token for nonexistent user
        assert data["details"] is None

    async def test_reset_password_success(
        self, client: AsyncClient, registered_user: dict
    ):
        """Test successful password reset."""
        # Get reset token
        forgot_response = await client.post(
            "/api/v1/auth/forgot-password",
            json={"email": registered_user["email"]},
        )
        reset_token = forgot_response.json()["details"]["reset_token"]

        # Reset password
        new_password = "NewSecurePass456"
        response = await client.post(
            "/api/v1/auth/reset-password",
            json={
                "token": reset_token,
                "new_password": new_password,
            },
        )

        assert response.status_code == 200
        assert "reset successfully" in response.json()["message"].lower()

        # Verify can login with new password
        login_response = await client.post(
            "/api/v1/auth/login",
            json={
                "email": registered_user["email"],
                "password": new_password,
            },
        )
        assert login_response.status_code == 200

    async def test_reset_password_token_single_use(
        self, client: AsyncClient, registered_user: dict
    ):
        """Test reset token can only be used once."""
        # Get reset token
        forgot_response = await client.post(
            "/api/v1/auth/forgot-password",
            json={"email": registered_user["email"]},
        )
        reset_token = forgot_response.json()["details"]["reset_token"]

        # First reset - should succeed
        response1 = await client.post(
            "/api/v1/auth/reset-password",
            json={
                "token": reset_token,
                "new_password": "NewSecurePass456",
            },
        )
        assert response1.status_code == 200

        # Second reset with same token - should fail
        response2 = await client.post(
            "/api/v1/auth/reset-password",
            json={
                "token": reset_token,
                "new_password": "AnotherPass789",
            },
        )
        assert response2.status_code == 400
        assert "already been used" in response2.json()["detail"].lower()

    async def test_reset_password_invalid_token(self, client: AsyncClient):
        """Test reset with invalid token fails."""
        response = await client.post(
            "/api/v1/auth/reset-password",
            json={
                "token": "invalid_token",
                "new_password": "NewSecurePass456",
            },
        )

        assert response.status_code == 400
