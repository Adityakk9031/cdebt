# Demo assets: the pull-request comment, before and after the quiz

The real GitHub Actions bot (`github-actions[bot]`) posted this comment on a demo pull request on
the author's **private test mirror** on 2026-10-22, then **edited the same comment in place** after
the quiz was taken (comment 6341758030, created 13:19:08Z, edited 13:21:19Z UTC). The repository is
private, so these images are **re-renders of the real comment text** with GitHub's own markdown
renderer and a GitHub-like card, not literal screenshots. The text is exact
(`comment_before_quiz.md`, `comment_after_quiz.md`).

| file | use |
|---|---|
| `comment_1_before_quiz_{light,dark}.png` | the bot's first comment: NOT UNDERSTOOD (`not_quizzed`) |
| `comment_2_after_quiz_{light,dark}.png` | the same comment after the quiz: understood, re-checked |
| `*_clean.png` | the same without the small provenance caption, for the video timeline |

Honest use in the video: it is fine to say "this is what the bot posts on the pull request"; do not
present these as a live screen capture. For a literal screenshot, open a pull request in your public
repository after release (the Action posts the same comment) and capture it with Cmd+Shift+4.
