"""
test_submission_cap.py , a submission cannot expand without limit in the store
==============================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_submission_cap.py

`referee.MAX_SUBMISSION_BYTES` caps a submission at 8 MB decoded and
`server/beta_storage.rules` caps the wire form at 2 MB, and N2 recorded that
neither of them helps: the store decompresses a submission before the referee
is handed it, so a payload small enough to pass the wire cap inflates inside
the store while nothing is measuring it. The bomb built here is 87 KB on the
wire and 64 MB decompressed.

The half that is easy to get wrong is the other one. A cap that refuses
everything passes every check a bomb can motivate, so every refusal below is
run beside a real galaxy that must still load: the fixture as it ships, and a
save the size of the largest galaxy in the repo. That second one compresses to
almost nothing, which is the shape of a bomb, and it has to be accepted: the
cap is on size and not on ratio, because a real save of repetitive galaxy
state compresses very well too.

Two bombs, because they take different paths through `decode_capped`. One
declares its true size in the wire form's leading dword and is refused before
anything is inflated. The other lies, declaring 1 KB, and can only be caught
by the decompression itself. A cap that trusted the header would pass the
second, and the second is the one an attacker sends.

Nothing here launches a client or touches a live galaxy. The HTTP half runs
`turn_server` on an ephemeral port against a temporary directory.
"""
import base64
import os
import struct
import sys
import tempfile
import threading
import tracemalloc
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'), os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp
import turn_store
from turn_store import HttpTurnStore, TurnStore

PASS, FAIL = [], []

TURN = 7
CAP = turn_store.MAX_DECODED_BYTES

# The fixture every other test in this directory uses, and the only save blob
# the checkout tracks.
GALAXY = os.path.join(ROOT, 'client', 'SinglePlayerGalaxy.dat')

# The largest galaxy measured in this repo, to the byte. H5 and N5 measured
# live submissions at 39 KB and 129 KB decoded, so this is the top of the real
# range and the size a cap has to leave room for.
LARGEST_REAL = 129853

# What the bomb expands to. Eight times the cap, so a refusal cannot be an
# accident of the cap happening to sit near it.
BOMB_BYTES = 64 * 1024 * 1024


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def raises(fn, *a):
    """What a call raised, by class name, or None."""
    try:
        fn(*a)
    except Exception as exc:                                # noqa: BLE001
        return type(exc).__name__
    return None


def decoded(wire):
    """The blob, or the name of what refused it.

    The control half of this file asserts that ordinary saves still load, and
    a cap set too low makes those calls raise. Turning the exception into a
    value is what makes a too-tight cap a readable failure rather than a
    traceback out of the middle of the run.
    """
    try:
        return turn_store.decode_capped(wire)
    except Exception as exc:                                # noqa: BLE001
        return type(exc).__name__


def section(tag, payload, version=0):
    head = (len(payload) & sp.SIZE_MASK) | (version << sp.VERSION_SHIFT)
    return tag + struct.pack('<I', head) + payload


def save_of_size(total: int, turn: int = TURN, filler=b'.') -> bytes:
    """A parseable save of exactly `total` bytes.

    Framed rather than padded: `check_save` reads the turn number out of
    `GLOB`, so a blob that was merely the right length would test the cap
    against something no store would accept anyway.
    """
    body = section(b'GLOB', struct.pack('<I', turn) + filler * 60)
    filling = total - 8 - len(body) - 8
    assert filling > 0
    blob = section(b'SAVE', body + section(b'KNPL', filler * filling))
    assert len(blob) == total, (len(blob), total)
    return blob


def bomb(declared: int = None, size: int = BOMB_BYTES) -> bytes:
    """A wire-form submission that expands to `size`.

    `declared` is what the leading dword claims, which the sender chooses and
    nothing verifies until the blob is out. None means tell the truth.
    """
    if declared is None:
        declared = size
    return base64.b64encode(struct.pack('<I', declared) +
                            zlib.compress(b'\0' * size))


LYING = bomb(declared=1024)
HONEST = bomb()


# ── the bomb is a bomb ───────────────────────────────────────────────────────
def test_the_payload_is_real():
    """Before anything is refused, show there is something worth refusing."""
    print('the bomb')
    check(f'the wire form is small enough to pass the 2 MB storage rule',
          len(LYING) < 2 * 1024 * 1024, True)
    tracemalloc.start()
    try:
        out = zlib.decompress(base64.b64decode(LYING)[4:])
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    check('and decompresses to 64 MB with no cap on it',
          len(out), BOMB_BYTES)
    check('which is what it costs the process to hold',
          peak > 4 * CAP, True)
    print(f'         {len(LYING):,} wire bytes, {len(out):,} decompressed, '
          f'{peak / 1048576:.0f} MB peak')


def test_decode_refuses_both_bombs():
    print('decode_capped refuses it, during the read')
    check('a bomb that lies about its size is refused',
          raises(turn_store.decode_capped, LYING), 'SubmissionTooLarge')
    check('and one that declares it is refused too',
          raises(turn_store.decode_capped, HONEST), 'SubmissionTooLarge')

    # The point of the incremental decompression. A refusal that arrived after
    # the blob was built would be a refusal that had already done the damage.
    tracemalloc.start()
    try:
        raises(turn_store.decode_capped, LYING)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    check('refusing it never holds more than a few times the cap',
          peak < 4 * CAP, True)
    check('and nothing near the 64 MB the payload asked for',
          peak < BOMB_BYTES // 2, True)
    print(f'         {peak / 1048576:.1f} MB peak while refusing')

    # The honest one is cheaper still, because the header is read first.
    tracemalloc.start()
    try:
        raises(turn_store.decode_capped, HONEST)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    check('a declared oversize is refused without inflating anything',
          peak < CAP, True)


def test_ordinary_galaxies_still_load():
    """The control. A cap that refused everything would pass everything above.
    """
    print('and an ordinary galaxy still loads')
    check('the cap leaves room for the largest galaxy measured',
          CAP >= LARGEST_REAL, True)
    big = save_of_size(LARGEST_REAL)
    wire = sp.encode_save(big)
    check(f'a {LARGEST_REAL:,} byte save decodes through the cap',
          decoded(wire), big)
    # It compresses to a fraction of a per cent, which is the ratio a bomb
    # has. Nothing here may judge a submission by how well it compressed.
    check('even though it compresses as hard as the bomb does',
          len(wire) * 200 < len(big), True)

    if not os.path.exists(GALAXY):
        print(f'  [SKIP] no fixture at {GALAXY}')
        return
    with open(GALAXY, 'rb') as f:
        real = f.read()
    check('the shipped fixture is a real save',
          real[:4] in sp.KNOWN_TAGS, True)
    check('and round-trips through the capped decode',
          decoded(sp.encode_save(real)), real)
    print(f'         fixture is {len(real):,} bytes decoded')

    if CAP < LARGEST_REAL:
        print('  [SKIP] the boundary cases, the cap is below a real galaxy')
        return
    # A blob the size of the cap itself is the boundary. One byte over is not.
    edge = save_of_size(CAP)
    check('a save of exactly the cap is accepted',
          decoded(sp.encode_save(edge)), edge)
    over = save_of_size(CAP + 1)
    check('one byte over is refused',
          raises(turn_store.decode_capped, sp.encode_save(over)),
          'SubmissionTooLarge')


def test_truncated_is_still_truncated():
    """A payload that ends early is not an oversized one, and the referee
    already screens for what `save_parser` raises on it."""
    print('a truncated payload is told apart from an oversized one')
    good = save_of_size(4096)
    wire = base64.b64decode(sp.encode_save(good))
    chopped = base64.b64encode(wire[:len(wire) - 20])
    check('a stream that ends early raises zlib.error, not the cap',
          raises(turn_store.decode_capped, chopped), 'error')
    check('and bytes that are not a stream at all raise too',
          raises(turn_store.decode_capped,
                 base64.b64encode(struct.pack('<I', 8) + b'nonsense')),
          'error')


# ── through the stores ───────────────────────────────────────────────────────
def planted(root, civ='Bomber', wire=LYING):
    """A store holding one honest submission and one bomb.

    Both, always. A store that refused the bomb by refusing everything would
    pass a test that only planted the bomb, and that is the failure this whole
    file is arranged around.
    """
    store = TurnStore(root)
    store.start(save_of_size(4096), ['DemoPlayer', civ], turn_seconds=1800)
    store.submit('DemoPlayer', TURN, save_of_size(8192, filler=b'o'))
    path = store.submission_path(civ, TURN)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(wire)
    return store


def test_directory_store():
    print('the directory store refuses it while reading it')
    root = os.path.join(tempfile.mkdtemp(prefix='cap_'), 'galaxy')
    store = planted(root)
    check('the bomb is listed, because listing costs nothing',
          store.submitted_civs(TURN), ['Bomber', 'DemoPlayer'])
    check('reading that one civ refuses',
          raises(store.submission, 'Bomber', TURN), 'SubmissionTooLarge')
    check('and the honest submission beside it still reads',
          store.submission('DemoPlayer', TURN), save_of_size(8192, filler=b'o'))
    check('reading them together refuses rather than inflating',
          raises(store.submissions, TURN), 'SubmissionTooLarge')

    tracemalloc.start()
    try:
        raises(store.submissions, TURN)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    check('and holds nothing like the 64 MB it was asked to',
          peak < BOMB_BYTES // 2, True)


def test_the_referee_drops_one_civ_not_the_turn():
    """N2's rule, applied to this. A bomb must cost its own sender the turn and
    nobody else theirs.

    `read_submissions` already falls back to reading the roster one civ at a
    time when the listing raises, which is what turns a refusal in the store
    into a dropped submission with a name on it. This checks that the new
    refusal lands on that path rather than somewhere the referee treats as
    fatal, and it reads the referee rather than changing it.
    """
    print('one bomb costs one player the turn')
    import referee
    root = os.path.join(tempfile.mkdtemp(prefix='cap_ref_'), 'galaxy')
    store = planted(root)
    notes, dropped = {}, {}
    got = referee.read_submissions(store, TURN, store.civs(), log=lambda _m: None,
                                   notes=notes, dropped=dropped)
    check('the honest player is still in the turn', sorted(got), ['DemoPlayer'])
    check('the bomber is dropped', 'Bomber' in dropped, True)
    check('and is told why', bool(notes.get('Bomber')), True)
    check('and nobody else is told anything',
          [c for c in notes if c != 'Bomber'], [])
    check('the reason names the limit rather than a stack trace',
          'limit' in ' '.join(notes.get('Bomber', [])), True)


def test_over_http():
    """The service and its store are a matched pair, so neither may inflate it.
    """
    print('the service does not inflate it either')
    import turn_server
    root = os.path.join(tempfile.mkdtemp(prefix='cap_http_'), 'galaxy')
    store = planted(root)
    kept = turn_server.VERBOSE
    turn_server.VERBOSE = False
    httpd = turn_server.serve(root, '127.0.0.1', 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        client = HttpTurnStore(f'http://127.0.0.1:{httpd.server_address[1]}')
        check('the names come back, both of them',
              client.submitted_civs(TURN), ['Bomber', 'DemoPlayer'])
        check('asking for the bomb is a refusal rather than 64 MB',
              raises(client.submission, 'Bomber', TURN) is not None, True)
        check('and the honest submission is served as it always was',
              client.submission('DemoPlayer', TURN),
              save_of_size(8192, filler=b'o'))

        # The other direction: a bomb served to a launcher that did not ask
        # for it from a store, which is the relay's shape, since there the
        # bytes come from Cloud Storage rather than from the service.
        check('a store handed the bomb straight off the wire refuses it',
              raises(turn_store.decode_capped, LYING), 'SubmissionTooLarge')
    finally:
        httpd.shutdown()
        httpd.server_close()
        turn_server.VERBOSE = kept


if __name__ == '__main__':
    test_the_payload_is_real()
    test_decode_refuses_both_bombs()
    test_ordinary_galaxies_still_load()
    test_truncated_is_still_truncated()
    test_directory_store()
    test_the_referee_drops_one_civ_not_the_turn()
    test_over_http()
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    if FAIL:
        for name in FAIL:
            print(f'  failed: {name}')
    sys.exit(1 if FAIL else 0)
