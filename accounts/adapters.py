from allauth.account.adapter import DefaultAccountAdapter


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request):
        # Nobody creates their own account; members join through an approved invitation.
        return False

    def pre_login(self, request, user, **kwargs):
        """A Permanently Banned account cannot sign in: the attempt shows a single page (rule 46)."""
        from django.shortcuts import render

        from moderation.permanent import is_permanently_banned

        if is_permanently_banned(user):
            return render(request, "account/permanently_banned.html", status=403)
        return super().pre_login(request, user, **kwargs)
