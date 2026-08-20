# Colour resolves to a Colour or to Unknown

Status: accepted

**Colour** resolution answers with a colour or with _Unknown_, and stops there.
It does not invent a fallback. The colour drawn for Unknown belongs to the
surface doing the drawing, and the two surfaces deliberately choose **different
ones**: the swatch draws grey, the glassware **Placeholder** draws amber.

This is written down because the end state **looks exactly like the bug it
fixes**. A reader who greps for the two constants finds a neutral grey in the
colour model and an amber in the glass renderer, concludes they are a drift bug,
and unifies them. That reading is how issue #11 was written - correct about the
mechanism, wrong about the fix. Before this decision the divergence was real:
three call sites each resolved Colour themselves and each carried its own
fallback, so a Beer with no EBC and no **Colour override** could render a grey
swatch beside an amber pour with nothing asserting they should agree.

The fix was to resolve **once**. The fallback was a separate question, and the
answer is that it is not one value.

## Decisions

### The fallback is the renderer's, not the model's

A swatch is a claim about the Beer: it says "this beer is this colour". When the
colour is unknown, grey is the honest answer - it reads as absent data, which is
what it is.

A Placeholder is an illustration of a glass of beer. A grey pour does not read as
"colour unknown"; it reads as a broken image. Amber reads as "a beer", which is
the most that can honestly be said about a Beer with no colour data.

Forcing one value onto both makes one of them wrong. So resolution reports
Unknown and each surface declares its own fallback at the point of rendering.
What the old code got wrong was not having two fallbacks - it was having three
_resolutions_.

The single resolution point is what this buys: a **known** Colour is now
guaranteed identical on the swatch and the Placeholder, because both read the
same answer. Only the Unknown case differs, and it differs on purpose.

Note that with default **Settings** the grey is rarely seen at all:
`hide_color_when_empty` defaults on, which suppresses the swatch outright when
Colour is Unknown. It becomes visible only when an operator turns that off.

### Saturation never mutes a Colour override

**Saturation** applies to the *computed* Colour only. A Colour override is an
exact instruction - an operator who writes `colour:#780606` gets `#780606`, and
a `saturation:60` alongside it is ignored rather than blended.

The alternative reading is defensible in the abstract: Saturation is a muting
factor, so it could apply to whatever Colour resolved to. It was rejected for two
reasons. If Saturation always applied there would be no way to ask for exactly
one colour, which is the entire point of an override. And changing it would
restyle every existing Beer carrying both tokens the next time the container
restarted - an operator-visible change to a board nobody asked to change.

Saturation exists to tame the EBC model's output on a TV panel. That is a
property of the computed branch, and it stays there.

### The swatch is not an Attribute

The colour swatch and the EBC **Attribute** share one operator toggle but ask
different questions about emptiness. The swatch asks whether Colour is *known* -
an EBC **or** an override. The EBC Attribute asks whether *EBC* is present. This
is why a Beer with only a Colour override shows a swatch and no EBC number, which
looks inconsistent until the two questions are named separately.

One toggle still drives both, because two checkboxes would be worse for the
operator than one. But they resolve to two separate answers, and both answers are
computed server-side rather than re-derived by the display.

## Consequences

The `/img/beer-glass` URL carries the **resolved** colour rather than the inputs
that produce it, so the glass renderer tints what it is told instead of
re-deriving Colour from EBC and Saturation. Unknown is expressed by sending no
colour at all, which is what selects the amber fallback.

A future reader who wants to unify the two fallbacks should read this ADR first
and then say so explicitly, per the repo's convention on contradicting an ADR.
