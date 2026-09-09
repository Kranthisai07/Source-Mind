# WORKING_STANDARDS.md

This document exists because this project has repeatedly found
that code which "looks correct" and "compiles clean" is not the
same as code that is actually correct. Every rule below was
learned from a real bug in this codebase, not written in the
abstract. Read the example, not just the rule.

## 1. Test the unauthorized case, not just the authorized one.

Every round of endpoint testing in this project's history
verified "does the right user get the right data." None of it
verified "does the wrong user get denied," until one deliberate
pass did. That pass found a live production data leak spanning
18 of 27 workspace-scoped routes, including 5 mutating endpoints
(a non-member could DELETE another workspace's connector and get
a 204 confirming it worked).

**Rule:** any access-controlled route is not verified until you
have tested it with a real unauthorized caller and confirmed
denial. A route that returns correct data to an authorized user
proves nothing about whether it's actually gated.

## 2. When testing "unauthorized," use two distinct kinds of outsider.

The first real test of the isolation bug above used a caller
with zero memberships anywhere. That's a weak test: a broken gate
that checks "does this user have ANY membership" would pass it
while still leaking to every real user who belongs to a different
workspace. The stronger test — a caller who IS a real member,
just of a DIFFERENT workspace — is what actually proves the gate
checks membership of the specific resource being accessed, not
just membership of something.

**Rule:** verify access control with both (a) a user with zero
memberships, and (b) a user who is a legitimate member of a
different resource. Testing only (a) can produce a false sense of
security.

## 3. Distinguish `null` from `undefined` explicitly. Do not rely
on default-parameter substitution to cover both.

`function f({ x = [] } = {})` only substitutes the default when
`x` is `undefined`. A real API in this project returns
`attribution: null` (not the field being absent), and that
crashed a component with `null.reduce()` because the default
never fired. This is a JavaScript language behavior, not a
one-off bug, it will recur anywhere a default-parameter
assumption meets a real API that explicitly returns null.

**Rule:** when consuming API data with a destructured default,
verify what the real API actually returns for the empty case, a
missing field and an explicit null are different and need
different handling.

## 4. Never trust deployment freshness by hostname or timestamp
alone. Verify by a counter that resets to zero.

A hostname changing after a deploy does not prove the old
process has stopped running, rolling deploys briefly run old and
new containers side by side, and a verification that fires during
that window can silently test the wrong code. This project was
burned by exactly this more than once before adopting the rule:
confirm freshness via something that provably resets (a task
counter at 0, not just a new container ID) and confirm the OLD
container is gone, not just that a new one exists.

**Rule:** "the hostname changed" is not proof of a fresh deploy.
"The task/request counter is at zero and there is exactly one
container" is.

## 5. A test that has never been observed to fail has not
demonstrated it can catch anything.

This project built several regression tests (an SQL bind-
parameter type checker, an enum-drift assertion for a frontend
color map) and, in each case, deliberately broke the underlying
code or injected a known-bad value to confirm the test actually
failed before trusting that it would catch a real regression
later.

**Rule:** after writing a test meant to catch a specific class of
bug, prove it can, by temporarily reintroducing that exact bug
and confirming the test fails, then revert. A test that has only
ever been seen passing is unproven.

## 6. Re-check your own prior conclusion against the actual
evidence before recording it as fact, especially in a
security-relevant record.

A near-miss claim was initially written into this project's
decision log with a plausible-sounding but factually wrong
account of how a bug was found. It was caught by re-reading the
actual transcript against the claim, not by re-deriving the
reasoning from scratch, and re-checking led directly to
discovering the true scope of the bug was five times larger than
first reported.

**Rule:** before writing a "how this was found" narrative into a
permanent record, verify it against the actual evidence (logs,
transcripts, test output), don't rely on a plausible
reconstruction, even one you believe.

## 7. A fix verified only against mocked or empty data is not
verified. Test against real, populated responses.

Several frontend bugs in this project (an unwrapped API response
wrapper, a wrong field name, a broken navigation link) only threw
when a real API call returned actual results, they were invisible
against an empty result set, which is what every earlier round of
testing had used.

**Rule:** when verifying a data-consuming component, test it
against a real, non-empty API response, not just the empty/error
state. A page that "renders fine" with no data has not been
tested.

## 8. Before assuming a fix is complete, check for the same class
of bug elsewhere in the same area.

Every "found one instance, fixed it" moment in this project's
history has been followed by a systematic sweep that found more
instances of the exact same pattern nearby, mock-era literals in
sibling pages, the same isoformat()-into-raw-SQL bug in a second
function, the same hardcoded weight-swap in a second document.

**Rule:** after fixing one instance of a bug, grep/sweep for the
same pattern across the rest of the codebase before considering
the class of bug closed, not just the one instance.

## 9. Tailwind's JIT compiler only sees literal class strings. A
template-literal or dynamically-constructed class name may compile
and render with no error while silently emitting no style at all.

A component built `!${t.cls}` expecting a semantic color modifier
to apply. It rendered with zero errors, zero warnings, the
component "worked" — the class was simply never generated into
the build's CSS, discovered only by checking the actual emitted
stylesheet against what the source code assumed would be there.

**Rule:** any Tailwind class name built dynamically (template
literals, string concatenation, computed values) must be verified
against the actual built CSS output, not assumed to work because
the component renders without error. Prefer inline styles or a
static lookup map over dynamic class construction when the value
comes from data, not a fixed enum.

## How to use this document

Read this before starting any non-trivial task in this codebase.
When you find a new, generalizable lesson the same way the nine
above were found, add it here with a real example, don't let it
live only in a commit message or a chat transcript.
