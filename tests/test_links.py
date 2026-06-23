"""Link extraction and links_of (#7)."""
import brain_mcp.vault as vault


def test_extract_outlinks_masks_code_and_dedupes():
    body = (
        "Links [[alpha]] and [[beta|Beta]] and [[gamma#sec]].\n"
        "Inline `[[nope]]` and:\n```\n[[also-nope]]\n```\n"
        "Repeat [[alpha]].\n"
    )
    assert vault.extract_outlinks(body) == ["alpha", "beta", "gamma"]


def test_links_of_outbound_inbound_and_dangling(write_note):
    write_note("notes", "a", {"type": "topic"}, "links to [[b]] and [[ghost]]")
    write_note("notes", "b", {"type": "topic"}, "back to [[a]]")
    links = vault.links_of("a")
    out_ids = {o["id"]: o["dangling"] for o in links["outbound"]}
    assert out_ids == {"b": False, "ghost": True}
    assert [i["id"] for i in links["inbound"]] == ["b"]


def test_links_symmetry(write_note):
    write_note("notes", "x", {"type": "topic"}, "to [[y]]")
    write_note("notes", "y", {"type": "topic"}, "leaf")
    assert any(i["id"] == "x" for i in vault.links_of("y")["inbound"])


def test_find_references_ignores_code_spans(write_note):
    write_note("notes", "target", {"type": "topic"}, "t")
    write_note("notes", "real-ref", {"type": "topic"}, "see [[target]]")
    write_note("notes", "code-ref", {"type": "topic"}, "example `[[target]]`")
    ref_ids = {n.id for n in vault.find_references("target")}
    assert "real-ref" in ref_ids
    assert "code-ref" not in ref_ids
