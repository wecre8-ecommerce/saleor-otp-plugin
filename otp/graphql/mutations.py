from datetime import timedelta
from typing import Optional
from urllib.parse import urlencode, urlparse

import graphene
from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.utils import timezone

from saleor.account import events as account_events
from saleor.account.notifications import get_default_user_payload
from saleor.core.notification.utils import get_site_context
from saleor.core.notify_events import NotifyEventType
from saleor.core.utils.url import validate_storefront_url
from saleor.graphql.account.mutations.authentication import CreateToken
from saleor.graphql.channel.utils import clean_channel, validate_channel
from saleor.graphql.core.mutations import BaseMutation
from saleor.graphql.core.types.common import Error
from otp.models import OTP
from otp.graphql.enums import OTPErrorCode, OTPErrorCodeType

User = get_user_model()


class OTPError(Error):
    code = OTPErrorCodeType(description="The error code.", required=True)


def send_password_reset_notification(
    user, manager, channel_slug: Optional[str], staff=False, reset_url=None
):
    otp = OTP.objects.create(user=user)

    payload = {
        "user": get_default_user_payload(user),
        "recipient_email": user.email,
        "token": str(otp),
        "channel_slug": channel_slug,
        **get_site_context(),
    }

    url_components = urlparse(reset_url)
    url_components = url_components._replace(
        query=urlencode(
            {
                "code": str(otp),
            }
        )
    )
    payload["reset_url"] = url_components.geturl()

    event = (
        NotifyEventType.ACCOUNT_STAFF_RESET_PASSWORD
        if staff
        else NotifyEventType.ACCOUNT_PASSWORD_RESET
    )
    manager.notify(event, payload=payload, channel_slug=channel_slug)


class RequestPasswordRecovery(BaseMutation):
    class Arguments:
        email = graphene.String(
            required=True,
            description="Email of the user that will be used for password recovery.",
        )
        redirect_url = graphene.String(
            required=True, description="Base URL for the email sent to the user."
        )
        channel = graphene.String(
            description=(
                "Slug of a channel which will be used for notify user. Optional when "
                "only one channel exists."
            )
        )

    class Meta:
        description = "Sends an email with the account password modification link."
        error_type_class = OTPError

    @classmethod
    def clean_user(cls, email):
        try:
            return User.objects.get(email=email)
        except ObjectDoesNotExist:
            raise ValidationError(
                {
                    "email": ValidationError(
                        "User with this email doesn't exist",
                        code=OTPErrorCode.USER_NOT_FOUND,
                    )
                }
            )

    @classmethod
    def perform_mutation(cls, _root, info, **data):
        email = data.get("email", "")
        channel = data.get("channel", "")
        redirect_url = data.get("redirect_url", "")

        user = cls.clean_user(email)

        if not user.is_staff:
            channel = clean_channel(channel, error_class=OTPErrorCode).slug
        elif channel is not None:
            channel = validate_channel(channel, error_class=OTPErrorCode).slug

        try:
            validate_storefront_url(redirect_url)
        except ValidationError as error:
            raise ValidationError(
                {"redirect_url": error}, code=OTPErrorCode.INVALID_URL
            )

        send_password_reset_notification(
            user,
            info.context.plugins,
            channel_slug=channel,
            staff=user.is_staff,
            reset_url=redirect_url,
        )
        return RequestPasswordRecovery()


class SetPasswordByCode(CreateToken):
    class Arguments:
        code = graphene.String(
            description="An OTP required to set the password.", required=True
        )
        email = graphene.String(required=True, description="Email of a user.")
        password = graphene.String(required=True, description="Password of a user.")

    class Meta:
        description = (
            "Sets the user's password from the token sent by email "
            "using the RequestPasswordRecovery mutation."
        )
        error_type_class = OTPError

    @classmethod
    def handle_used_otp(cls, otp: OTP):
        if otp.is_used:
            raise ValidationError(
                {
                    "code": ValidationError(
                        "Invalid or expired OTP supplied",
                        code=OTPErrorCode.INVALID.value,
                    )
                }
            )

    @classmethod
    def handle_expired_otp(cls, otp: OTP):
        if otp.issued_at + timedelta(minutes=15) <= timezone.now():
            raise ValidationError(
                {
                    "code": ValidationError(
                        "Invalid or expired OTP supplied",
                        code=OTPErrorCode.INVALID.value,
                    )
                }
            )

    @classmethod
    def get_user(cls, _info, data):
        email = data["email"]

        try:
            return User.objects.get(email=email)
        except ObjectDoesNotExist:
            raise ValidationError(
                {
                    "email": ValidationError(
                        "User doesn't exist", code=OTPErrorCode.USER_NOT_FOUND
                    )
                }
            )

    @classmethod
    def validate_otp(cls, user, code):
        try:
            otp = OTP.objects.get(code=code, user=user)
        except OTP.DoesNotExist:
            raise ValidationError(
                "Invalid or expired OTP supplied", code=OTPErrorCode.INVALID
            )

        cls.handle_used_otp(otp)
        cls.handle_expired_otp(otp)

    @classmethod
    def _set_password_for_user(cls, user, password, code):
        cls.validate_otp(user, code)

        try:
            pass
            password_validation.validate_password(password, user)
        except ValidationError as error:
            raise ValidationError({"password": error})

        user.set_password(password)
        user.save(update_fields=["password"])
        account_events.customer_password_reset_event(user=user)

        otp = OTP.objects.get(code=code, user=user)
        otp.is_used = True
        otp.save(update_fields=["is_used"])

    @classmethod
    def perform_mutation(cls, root, info, **data):
        code = data["code"]
        password = data["password"]

        try:
            user = cls.get_user(info, data)
            cls._set_password_for_user(user, password, code)
        except ValidationError as e:
            return cls.handle_errors(e)
        return super().perform_mutation(root, info, **data)
