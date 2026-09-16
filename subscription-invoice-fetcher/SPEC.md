# Gmail App Password Sign-In Fix

## What it does
Fixes sign-in so a Gmail App Password copy-pasted with the spaces Google shows for readability (like "abcd efgh ijkl mnop") works correctly, by removing all spaces before it's used. The on-page help text is also updated to tell users spaces are fine and will be removed automatically.

## What you give it
No new inputs. You still enter the same **Email address** and **Gmail App Password** as before, on the same sign-in page.

## What it gives you back
No new output. The tool signs in and fetches invoices exactly as it does today — the only visible difference is that pasting the password with its original spacing now works, and the help text under the password field explains this.

## Rules it follows
1. Before the app password is used to sign in, every space inside it is removed — not just spaces at the start or end — so "abcd efgh ijkl mnop" and "abcdefghijklmnop" are treated as the same password.
2. If, after removing spaces, the password is not 16 characters, sign-in fails with the same "invalid app password" message shown today — no new error message is introduced.
3. The help text next to the App Password field is updated to say that spaces are fine to leave in when pasting and will be stripped automatically.
4. Nothing else about sign-in changes: the password is still never written to disk or logged, and it is still held only in memory for the current run.

## What I am assuming
- Only spaces need stripping — App Passwords don't contain tabs or other whitespace, but if any sneak in (e.g. from a paste), those are stripped too since the fix strips all whitespace, not just spaces.
- The wording of the updated help text is not prescribed by you; I'll write a short, plain sentence and you can request changes to it.
- No other part of the sign-in flow, the invoice search, or the output changes — this is a two-line fix plus a text update, nothing else in the tool is touched.

## Not included
- No change to how invoices are found, formatted, or exported — that logic is untouched.
- No validation that the email address is a real Gmail address — that's unchanged from today.
- No stronger error message that would tell the user specifically "your password has the wrong number of characters" — today's generic failure message stays as is, unless you tell me otherwise.

## How you will know it works
1. Generate a fresh App Password from Google, copy it exactly as Google displays it (with spaces), paste it into the field, and confirm sign-in succeeds.
2. Remove the spaces yourself and paste the 16-character password with no spaces — confirm sign-in still succeeds.
3. Look at the password field's help text and confirm it now mentions that spaces are fine.
4. Enter a deliberately wrong password (right length, wrong characters) and confirm you still get the same failure message as before — nothing else changed.