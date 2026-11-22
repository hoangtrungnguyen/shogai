import pytest

from shogai.store import NotFound, StoreError, slugify, split_frontmatter, validate_slug


def test_slugify_handles_scripts_and_diacritics():
    assert slugify("Jane Doe") == "jane-doe"
    assert slugify("Nguyễn Văn Đạt") == "nguyen-van-dat"
    assert slugify("José Ñúñez_Jr.") == "jose-nunez-jr"
    assert slugify("田中 太郎") == "田中-太郎"
    assert slugify("ガギ") == "ガギ"  # Japanese voicing marks are kept
    assert slugify(" --- ") == ""


def test_create_list_and_unique_slugs(store):
    a = store.create("Jane Doe", relationship="college friend", aliases=["JD"], tags=["hiking"])
    b = store.create("Jane Doe")
    assert (a.slug, b.slug) == ("jane-doe", "jane-doe-2")
    jane = store.get("jane-doe")
    assert jane.relationship == "college friend"
    assert jane.aliases == ["JD"] and jane.tags == ["hiking"]
    assert jane.body.startswith("# Jane Doe")
    assert [p.slug for p in store.list_people()] == ["jane-doe", "jane-doe-2"]
    with pytest.raises(StoreError):
        store.create("   ")


def test_frontmatter_keeps_timestamps_as_strings(store):
    person = store.create("Ana")
    meta, body = split_frontmatter(person.raw)
    assert isinstance(meta["created"], str) and meta["created"].startswith("20")
    assert body.lstrip().startswith("# Ana")
    assert "## Source Log" in person.raw and "## Source Log" not in person.body


def test_sources_ids_and_managed_source_log(store):
    person = store.create("Ana")
    r1 = store.add_source(person.slug, kind="image", title="Beach day", summary="At the beach.", markdown="# Beach day\n")
    r2 = store.add_source(person.slug, kind="text", title="My thoughts", summary="Notes.", markdown="# My thoughts\n")
    assert (r1.id, r2.id) == ("s1", "s2")
    assert r1.filename.startswith("s001-image-beach-day")

    saved = store.save_profile(person.slug, body="# Ana\n\n- Loves the sea [s1]\n\n## Source Log\nstale junk")
    assert saved.source_count == 2
    assert "stale junk" not in saved.raw
    assert "**[s1]**" in saved.raw and "**[s2]**" in saved.raw
    assert saved.body.endswith("Loves the sea [s1]")

    record, text = store.get_source(person.slug, "S2")
    assert record.title == "My thoughts" and "# My thoughts" in text
    with pytest.raises(NotFound):
        store.get_source(person.slug, "s9")


def test_save_profile_backs_up_previous_version(store):
    person = store.create("Ana")
    store.save_profile(person.slug, body="# Ana\n\nv2")
    store.save_profile(person.slug, body="# Ana\n\nv3")
    history = sorted((store.person_dir(person.slug) / "history").glob("profile-*.md"))
    assert len(history) == 2
    assert "v2" in history[-1].read_text()


def test_write_raw_validates_and_preserves_created(store):
    person = store.create("Ana")
    with pytest.raises(StoreError):
        store.write_raw(person.slug, "no frontmatter at all")
    updated = store.write_raw(person.slug, "---\nname: Ana Lima\ntags: tea, books\n---\n\n# Ana Lima\n\nHand edit.")
    assert updated.name == "Ana Lima"
    assert updated.tags == ["tea", "books"]
    assert updated.created == person.created
    assert updated.body.endswith("Hand edit.")


def test_delete_moves_folder_to_trash(store):
    person = store.create("Ana")
    trash = store.delete(person.slug)
    assert trash.exists() and not store.exists(person.slug)
    assert trash.parent == store.trash_dir


def test_resolve_by_slug_name_alias_and_partial(store):
    store.create("Jane Doe", aliases=["JD"])
    store.create("John Smith")
    assert store.resolve("jane-doe") == "jane-doe"
    assert store.resolve("Jane Doe") == "jane-doe"
    assert store.resolve("jd") == "jane-doe"
    assert store.resolve("smith") == "john-smith"
    assert store.resolve("j") is None  # ambiguous
    assert store.resolve("nobody") is None


def test_search_finds_profile_and_source_lines(store):
    person = store.create("Ana")
    store.save_profile(person.slug, body="# Ana\n\n- Birthday: 12 March [s1]\n- Loves matcha")
    store.add_source(person.slug, kind="text", title="Chat", summary="", markdown="She is allergic to peanuts.")
    hits = store.search("birthday")
    assert hits and hits[0]["person"] == "ana" and "12 March" in hits[0]["snippet"]
    hits = store.search("allergic peanuts")
    assert hits[0]["source_id"] == "s1"
    assert store.search("x") == []


def test_media_and_slug_traversal_is_rejected(store):
    person = store.create("Ana")
    path = store.new_media_path(person.slug, "../../evil photo.JPG")
    assert path.parent == store.media_dir(person.slug)
    assert path.name.endswith("evil-photo.jpg")
    path.write_bytes(b"x")
    assert store.media_file(person.slug, path.name) == path.resolve()
    with pytest.raises(NotFound):
        store.media_file(person.slug, "../profile.md")
    for bad in ["../ana", ".hidden", "a/b", ""]:
        with pytest.raises(NotFound):
            validate_slug(bad)
