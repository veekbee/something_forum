from allauth.account.forms import ResetPasswordForm as AllauthResetPasswordForm
from allauth.account.models import EmailAddress


class ResetPasswordForm(AllauthResetPasswordForm):
    """Send reset links only to an address verified on the account: whoever controls a verified
    address is, in effect, the account's owner. allauth would otherwise fall back to unverified
    addresses."""

    def clean_email(self):
        email = super().clean_email()
        verified = set(
            EmailAddress.objects.filter(email__iexact=email, verified=True).values_list("user_id", flat=True)
        )
        self.users = [user for user in self.users if user.pk in verified]
        return email
