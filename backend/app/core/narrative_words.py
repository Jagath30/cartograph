"""The narrative's vocabulary: plain words for the roles a key plays
(FR-26, criterion 14; the owner's ruling at stop 2 of step 8).

WRITTEN BY HAND, small, and read by the narrative ALONE. Ingestion does
not read it: the descriptions of DD-08, the embeddings and the snapshot
are what they were, and nothing here can move a score or a hash. A test
holds that no other module imports this one.

It works on the READABLE names ingestion already built ("bill customer
demographics surrogate key"), not on identifiers, so it is not tied to
the spelling of one schema's columns; the words themselves are a
retailer's. Where it has no phrase the narrative falls back to the
readable name without "surrogate key" (ruling 7), so a gap reads plainly
and is never an error.

  ROLES     a word that stands before the thing a key points to, and how
            it is said: the bill customer -> the billed customer.
  PAIRS     a role and a thing that read better as one phrase.
  MOMENTS   a key to a date or a time, whole: sold date -> date of sale.
  TABLES    a table's plain name where the readable one is jargon.
  ONE       one record of a table: web sales -> web sale.
  THINGS    a table as something a record has: customer demographics ->
            customer's demographics.
"""

# Said at the end of a readable key name, and dropped.
KEY_SUFFIX = " surrogate key"

ROLES: dict[str, str] = {
    "bill": "billed",
    "ship": "shipped-to",
    "refunded": "refunded",
    "returning": "returning",
    "current": "current",
}

PAIRS: dict[tuple[str, str], str] = {
    ("bill", "customer address"): "billing address",
    ("ship", "customer address"): "shipping address",
    ("current", "customer address"): "current address",
    ("refunded", "customer address"): "refunded customer's address",
    ("returning", "customer address"): "returning customer's address",
}

MOMENTS: dict[str, str] = {
    "date": "date",
    "sold date": "date of sale",
    "ship date": "ship date",
    "returned date": "date of return",
    "sold time": "time of sale",
    "returned time": "time of return",
    "return time": "time of return",
    "first sales date": "date of first purchase",
    "first ship-to date": "date of first shipment",
    "last review date": "date of last review",
    "open date": "opening date",
    "closed date": "closing date",
    "close date": "closing date",
    "start date": "start date",
    "end date": "end date",
    "access date": "date of last access",
    "creation date": "creation date",
}

TABLES: dict[str, str] = {
    "date dimension": "calendar dates",
    "time dimension": "times of day",
}

ONE: dict[str, str] = {
    "web sales": "web sale",
    "catalog sales": "catalog sale",
    "store sales": "store sale",
    "web returns": "web return",
    "catalog returns": "catalog return",
    "store returns": "store return",
    "inventory": "inventory count",
    "date dimension": "calendar date",
    "time dimension": "time of day",
    "customer demographics": "set of customer demographics",
    "household demographics": "set of household demographics",
}

THINGS: dict[str, str] = {
    "customer demographics": "customer's demographics",
    "household demographics": "household's demographics",
    "customer address": "address",
}
