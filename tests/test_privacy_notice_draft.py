"""Build step 9: the draft privacy notice reflects the Privacy section's new positions."""


def test_the_draft_privacy_notice_mentions_export_erasure_and_retention(client, db):
    page = client.get("/legal/privacy/").content.decode()
    for words in ("other members' messages", "Former member", "removed in full", "retention",
                  "survives erasure", "quoted from a removed post", "Notes staff wrote are kept"):
        assert words in page.replace("&#x27;", "'")
