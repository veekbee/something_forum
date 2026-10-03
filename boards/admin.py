from django.contrib import admin

from boards.models import Attachment, Post, PostRevision, SubForum, Thread, ThreadParticipant
from core.admin_site import ReadOnlyAdmin


@admin.register(SubForum)
class SubForumAdmin(ReadOnlyAdmin):
    list_display = ["name", "slug", "position", "is_archived"]


@admin.register(Thread)
class ThreadAdmin(ReadOnlyAdmin):
    list_display = ["title", "kind", "subforum", "author", "state", "post_count", "last_post_at"]
    list_filter = ["kind", "state"]


@admin.register(Post)
class PostAdmin(ReadOnlyAdmin):
    list_display = ["pk", "thread", "author", "created_at", "is_held", "rejected_at", "deleted_at"]


for model in (ThreadParticipant, PostRevision, Attachment):
    admin.site.register(model, ReadOnlyAdmin)
