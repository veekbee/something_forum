from django.urls import path

from boards import views

urlpatterns = [
    path("", views.forum_index, name="home"),
    path("f/<slug:slug>/", views.subforum_page, name="subforum"),
    path("f/<slug:slug>/new/", views.new_thread, name="new_thread"),
    path("t/<int:pk>/", views.thread_page, name="thread"),
    path("t/<int:pk>/reply/", views.reply, name="reply"),
    path("t/<int:pk>/title/", views.edit_title, name="edit_title"),
    path("t/<int:pk>/titles/", views.title_revisions, name="title_revisions"),
    path("t/<int:pk>/move/", views.move_thread, name="move_thread"),
    path("t/<int:pk>/end/<str:how>/", views.end_thread, name="end_thread"),
    path("t/<int:pk>/<str:what>/", views.toggle, name="thread_toggle"),
    path("p/<int:pk>/", views.post_link, name="post_link"),
    path("p/<int:pk>/edit/", views.edit_post, name="edit_post"),
    path("p/<int:pk>/delete/", views.delete_post, name="delete_post"),
    path("p/<int:pk>/revisions/", views.post_revisions, name="post_revisions"),
    path("p/<int:pk>/<str:decision>/", views.moderate_post, name="moderate_post"),
    path("members/autocomplete/", views.mention_autocomplete, name="mention_autocomplete"),
    path("members/<slug:slug>/", views.member_profile, name="member_profile"),
    path("search/", views.search, name="search"),
    path("attachments/<int:pk>/", views.attachment, name="attachment"),
]
