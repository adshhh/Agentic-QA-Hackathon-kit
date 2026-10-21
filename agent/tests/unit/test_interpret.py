from qa_agent.evaluation import interpret_recall
from qa_agent.interpret import quote_in_prd, repair_quote
from qa_agent.schemas import Requirement

PRD = "The landing page provides a clearly labeled **Enter Demo** action. It works at a 1280 × 800 viewport."


def test_quote_matching_ignores_markdown_and_whitespace():
    assert quote_in_prd("provides a clearly labeled Enter Demo action", PRD)
    assert quote_in_prd("It works at a 1280 x 800   viewport.", PRD)


def test_quote_matching_rejects_invented_text():
    assert not quote_in_prd("The landing page requires a password.", PRD)


def test_quote_matching_accepts_ellipsis_fragments():
    assert quote_in_prd("The landing page provides ... a 1280 × 800 viewport", PRD)


def test_quote_matching_accepts_multiple_real_sentences():
    assert quote_in_prd("The landing page provides a clearly labeled Enter Demo action. It works at a "
                        "1280 × 800 viewport.", PRD)


def test_repair_replaces_near_miss_with_real_sentence():
    fixed = repair_quote("The landing page provides a clearly-labelled Enter Demo action", PRD)
    assert fixed == "The landing page provides a clearly labeled **Enter Demo** action."


def test_repair_refuses_invented_quote():
    assert repair_quote("Users must verify their email before transferring money.", PRD) is None


def test_recall_counts_covered_inventory_items():
    req = Requirement(
        id="TRF-03", area="transfers", statement="s",
        prd_quote="Same-account transfers must be rejected with a clear explanation",
        observable_outcomes=[], verifiable_in_browser=True,
    )
    result = interpret_recall([req])
    assert result["covered"] == 1
    assert "TRF-3" not in result["missed"]
