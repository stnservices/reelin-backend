"""Internationalization (i18n) support for API error messages."""

from typing import Dict, Optional
from fastapi import Request

# Supported locales
SUPPORTED_LOCALES = ["en", "ro"]
DEFAULT_LOCALE = "en"

# Translation dictionaries for error messages
TRANSLATIONS: Dict[str, Dict[str, str]] = {
    "en": {
        # Authentication errors
        "invalid_credentials": "Invalid email or password",
        "email_exists": "An account with this email already exists",
        "email_not_found": "No account found with this email",
        "token_expired": "Your session has expired. Please login again",
        "token_invalid": "Invalid authentication token",
        "refresh_token_invalid": "Invalid refresh token",
        "invalid_token_type": "Invalid token type",
        "token_revoked": "Token has been revoked",
        "password_too_weak": "Password does not meet security requirements",
        "account_disabled": "This account has been disabled",
        "account_deactivated": "Account is deactivated",
        "email_verification_required": "Email verification required",
        "invalid_reset_token": "Invalid or expired reset token",
        "reset_token_used": "Reset token has already been used",
        "invalid_auth_header": "Invalid authorization header",
        "authentication_required": "Authentication required",
        "could_not_validate_credentials": "Could not validate credentials",

        # Authorization errors
        "forbidden": "You do not have permission to perform this action",
        "admin_required": "Administrator access required",
        "organizer_required": "Organizer access required",
        "validator_required": "Validator access required",
        "owner_required": "Only the owner can perform this action",
        "missing_roles": "Missing required roles: {roles}",
        "event_owner_or_admin_required": "Only the event owner or administrator can perform this action",
        "event_owner_validator_admin_required": "Access denied. Must be event owner, assigned validator, or administrator",

        # Validation errors
        "validation_error": "Invalid request data",
        "required_field": "This field is required",
        "invalid_email": "Please enter a valid email address",
        "invalid_date": "Please enter a valid date",
        "invalid_number": "Please enter a valid number",
        "value_too_small": "Value is below the minimum allowed",
        "value_too_large": "Value exceeds the maximum allowed",
        "invalid_roles": "Invalid roles: {roles}",

        # Resource errors
        "not_found": "Resource not found",
        "user_not_found": "User not found",
        "profile_not_found": "Profile not found",
        "event_not_found": "Event not found",
        "club_not_found": "Club not found",
        "species_not_found": "Species not found",
        "catch_not_found": "Catch not found",
        "participant_not_found": "Participant not found",
        "enrollment_not_found": "Enrollment not found",

        # Event lifecycle errors
        "event_not_active": "This event is not currently active",
        "event_not_open": "This event is not open for registration",
        "event_already_started": "This event has already started",
        "event_already_completed": "This event has already completed",
        "event_cancelled": "This event has been cancelled",
        "registration_closed": "Registration deadline has passed",
        "event_full": "Event has reached maximum capacity",
        "already_registered": "You are already registered for this event",
        "already_enrolled": "Already enrolled in this event",
        "not_registered": "You are not registered for this event",

        # Catch/validation errors
        "catch_not_editable": "This catch can no longer be edited",
        "catch_already_validated": "This catch has already been validated",
        "catch_not_pending": "This catch is not pending validation",
        "catch_not_approved": "This catch has not been approved",
        "rejection_reason_required": "Please provide a reason for rejection",
        "removal_reason_required": "Please provide a reason for removal",
        "invalid_species": "Invalid or inactive species selected",
        "below_min_length": "Catch length is below minimum legal length",

        # Club errors
        "club_name_exists": "A club with this name already exists",
        "already_club_member": "User is already a member of this club",
        "not_club_member": "User is not a member of this club",
        "cannot_remove_owner": "Cannot remove the club owner",
        "cannot_leave_as_owner": "Transfer ownership before leaving the club",

        # User errors
        "cannot_change_own_status": "Cannot change your own account status",

        # File upload errors
        "file_too_large": "File size exceeds the maximum allowed (10MB)",
        "invalid_file_type": "Invalid file type. Allowed: JPG, PNG",
        "upload_failed": "File upload failed. Please try again",

        # Generic errors
        "server_error": "An unexpected error occurred. Please try again later",
        "rate_limited": "Too many requests. Please wait before trying again",
    },
    "ro": {
        # Authentication errors
        "invalid_credentials": "Email sau parolă invalidă",
        "email_exists": "Există deja un cont cu acest email",
        "email_not_found": "Nu a fost găsit niciun cont cu acest email",
        "token_expired": "Sesiunea a expirat. Vă rugăm să vă autentificați din nou",
        "token_invalid": "Token de autentificare invalid",
        "refresh_token_invalid": "Token de reîmprospătare invalid",
        "invalid_token_type": "Tip de token invalid",
        "token_revoked": "Token-ul a fost revocat",
        "password_too_weak": "Parola nu îndeplinește cerințele de securitate",
        "account_disabled": "Acest cont a fost dezactivat",
        "account_deactivated": "Contul este dezactivat",
        "email_verification_required": "Este necesară verificarea email-ului",
        "invalid_reset_token": "Token de resetare invalid sau expirat",
        "reset_token_used": "Token-ul de resetare a fost deja folosit",
        "invalid_auth_header": "Header de autorizare invalid",
        "authentication_required": "Este necesară autentificarea",
        "could_not_validate_credentials": "Nu s-au putut valida credențialele",

        # Authorization errors
        "forbidden": "Nu aveți permisiunea de a efectua această acțiune",
        "admin_required": "Este necesar acces de administrator",
        "organizer_required": "Este necesar acces de organizator",
        "validator_required": "Este necesar acces de validator",
        "owner_required": "Doar proprietarul poate efectua această acțiune",
        "missing_roles": "Roluri lipsă necesare: {roles}",
        "event_owner_or_admin_required": "Doar proprietarul evenimentului sau administratorul poate efectua această acțiune",
        "event_owner_validator_admin_required": "Acces interzis. Trebuie să fiți proprietarul evenimentului, validator asignat sau administrator",

        # Validation errors
        "validation_error": "Date de solicitare invalide",
        "required_field": "Acest câmp este obligatoriu",
        "invalid_email": "Introduceți o adresă de email validă",
        "invalid_date": "Introduceți o dată validă",
        "invalid_number": "Introduceți un număr valid",
        "value_too_small": "Valoarea este sub minimul permis",
        "value_too_large": "Valoarea depășește maximul permis",
        "invalid_roles": "Roluri invalide: {roles}",

        # Resource errors
        "not_found": "Resursa nu a fost găsită",
        "user_not_found": "Utilizatorul nu a fost găsit",
        "profile_not_found": "Profilul nu a fost găsit",
        "event_not_found": "Evenimentul nu a fost găsit",
        "club_not_found": "Clubul nu a fost găsit",
        "species_not_found": "Specia nu a fost găsită",
        "catch_not_found": "Captura nu a fost găsită",
        "participant_not_found": "Participantul nu a fost găsit",
        "enrollment_not_found": "Înscrierea nu a fost găsită",

        # Event lifecycle errors
        "event_not_active": "Acest eveniment nu este activ în prezent",
        "event_not_open": "Acest eveniment nu este deschis pentru înscrieri",
        "event_already_started": "Acest eveniment a început deja",
        "event_already_completed": "Acest eveniment s-a terminat deja",
        "event_cancelled": "Acest eveniment a fost anulat",
        "registration_closed": "Termenul de înscriere a trecut",
        "event_full": "Evenimentul a atins capacitatea maximă",
        "already_registered": "Sunteți deja înscris la acest eveniment",
        "already_enrolled": "Deja înscris la acest eveniment",
        "not_registered": "Nu sunteți înscris la acest eveniment",

        # Catch/validation errors
        "catch_not_editable": "Această captură nu mai poate fi editată",
        "catch_already_validated": "Această captură a fost deja validată",
        "catch_not_pending": "Această captură nu este în așteptarea validării",
        "catch_not_approved": "Această captură nu a fost aprobată",
        "rejection_reason_required": "Vă rugăm să furnizați un motiv pentru respingere",
        "removal_reason_required": "Vă rugăm să furnizați un motiv pentru eliminare",
        "invalid_species": "Specie selectată invalidă sau inactivă",
        "below_min_length": "Lungimea capturii este sub lungimea legală minimă",

        # Club errors
        "club_name_exists": "Există deja un club cu acest nume",
        "already_club_member": "Utilizatorul este deja membru al acestui club",
        "not_club_member": "Utilizatorul nu este membru al acestui club",
        "cannot_remove_owner": "Nu se poate elimina proprietarul clubului",
        "cannot_leave_as_owner": "Transferați proprietatea înainte de a părăsi clubul",

        # User errors
        "cannot_change_own_status": "Nu puteți schimba starea propriului cont",

        # File upload errors
        "file_too_large": "Dimensiunea fișierului depășește maximul permis (10MB)",
        "invalid_file_type": "Tip de fișier invalid. Permise: JPG, PNG",
        "upload_failed": "Încărcarea fișierului a eșuat. Încercați din nou",

        # Generic errors
        "server_error": "A apărut o eroare neașteptată. Încercați mai târziu",
        "rate_limited": "Prea multe cereri. Așteptați înainte de a încerca din nou",
    },
}


def get_locale(request: Request) -> str:
    """
    Extract locale from Accept-Language header.

    Parses headers like "ro-RO,ro;q=0.9,en;q=0.8" to get the primary language.
    Falls back to English if the detected language is not supported.
    """
    accept_lang = request.headers.get("Accept-Language", DEFAULT_LOCALE)

    # Parse the primary language from Accept-Language header
    # Format: "ro-RO,ro;q=0.9,en;q=0.8" -> "ro"
    primary_lang = accept_lang.split(",")[0].split("-")[0].split(";")[0].lower().strip()

    return primary_lang if primary_lang in SUPPORTED_LOCALES else DEFAULT_LOCALE


def translate(key: str, locale: str, **kwargs) -> str:
    """
    Get translated message for a key.

    Falls back to English if the key is not found in the requested locale.
    Supports placeholder substitution with kwargs.

    Args:
        key: The translation key
        locale: The target locale (e.g., "en", "ro")
        **kwargs: Placeholder values for string formatting

    Returns:
        The translated message, or the key itself if not found
    """
    # Get translations for the requested locale, fall back to English
    translations = TRANSLATIONS.get(locale, TRANSLATIONS.get(DEFAULT_LOCALE, {}))

    # Get the message, fall back to English, then to the key itself
    message = translations.get(key)
    if message is None:
        message = TRANSLATIONS.get(DEFAULT_LOCALE, {}).get(key, key)

    # Apply placeholder substitution if kwargs provided
    if kwargs:
        try:
            message = message.format(**kwargs)
        except KeyError:
            pass  # If formatting fails, return the unformatted message

    return message


def get_error_message(key: str, request: Optional[Request] = None, **kwargs) -> str:
    """
    Convenience function to get a localized error message.

    Args:
        key: The error message key
        request: The FastAPI request object (optional, for locale detection)
        **kwargs: Placeholder values for string formatting

    Returns:
        The localized error message
    """
    locale = get_locale(request) if request else DEFAULT_LOCALE
    return translate(key, locale, **kwargs)
