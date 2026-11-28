// The share page (share.html), /r/<share_id>.

// The card, read-only: no share button, as whoever opens the link is just looking. Then the
// round is played again above it (see replay.js), and the play button appears at the end.
const record = JSON.parse(document.getElementById("record").textContent);
document.getElementById("message").hidden = !document.getElementById("message").textContent;
if (record) {
  const card = bestRoundCard.makeCard(record, { share: false });
  document.getElementById("card").append(card);
  replayRound({
    record,
    card,
    canvas: document.getElementById("canvas"),
    status: document.getElementById("replay-status"),
    skip: document.getElementById("skip"),
    again: document.getElementById("again"),
    play: document.getElementById("play"),
  });
}
