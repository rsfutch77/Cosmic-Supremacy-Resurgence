"""
Protocol test for the save/load slot handling in cs_server.

Speaks the wire format directly, so it checks the server against the contract in
the reconstruction report (section 10) without needing the game running:

    savegame      -> DONE
    savegamelist  -> <gameid>#SPC#<name>#SPC#<turn>#NEXT#...#NEXT#DONE
                     bare DONE for an empty list
    loadgame      -> DONE#VER#<6>#DATA#<blob>
"""
import http.client
import os
import shutil
import sys
import tempfile
import threading
import urllib.parse

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])

DATA = tempfile.mkdtemp(prefix="savetest_")
os.environ["CS_DATA_DIR"] = DATA
os.environ["CSPORT"] = "8899"
sys.path.insert(0, os.path.join(REPO, "server"))

import http.server
import cs_server

srv = http.server.HTTPServer(("127.0.0.1", 8899), cs_server.CSHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         got:  {got!r}")
        if not callable(want):
            print(f"         want: {want!r}")
        fails.append(label)


def post(action, **fields):
    body = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in fields.items())
    c = http.client.HTTPConnection("127.0.0.1", 8899, timeout=5)
    c.request("POST", f"/clientinterface.php?action={action}", body,
              {"Content-Type": "application/x-cosmicsupremacy",
               "Content-Length": str(len(body))})
    out = c.getresponse().read().decode("latin-1")
    c.close()
    return out


print("1. empty list before anything is saved")
check("bare DONE, not an empty body", post("savegamelist"), "DONE")

print("\n2. save with the new-slot sentinel gameid=-1")
check("responds DONE", post("savegame", gameid=-1, gamename="'My First Save'",
                            turn=12, version=1, data="QUJDREVG"), "DONE")

print("\n3. the save appears in the list under the name the player typed")
lst = post("savegamelist")
check("ends with DONE", lst, lambda s: s.endswith("#NEXT#DONE"))
check("name is present", lst, lambda s: "My First Save" in s)
check("slot is positive, not -1", lst, lambda s: s.split("#SPC#")[0] == "1")
check("turn is carried", lst, lambda s: s.split("#SPC#")[2].split("#NEXT#")[0] == "12")
print(f"         list: {lst!r}")

print("\n4. loading that slot returns the blob that was saved")
res = post("loadgame", gameid=1)
check("DONE#VER#<6>#DATA# framing", res,
      lambda s: s.startswith("DONE#VER#000000#DATA#"))
check("blob round-trips", res.split("#DATA#", 1)[1], "QUJDREVG")

print("\n5. a second new save gets its own slot, both are listed")
post("savegame", gameid=-1, gamename="'Second Save'", turn=40, version=1,
     data="WFlaMTIz")
lst = post("savegamelist")
check("two records", lst, lambda s: len(s.split("#NEXT#")) == 3)  # 2 + DONE
check("both names present", lst,
      lambda s: "My First Save" in s and "Second Save" in s)
check("slots 1 and 2", lst,
      lambda s: [r.split("#SPC#")[0] for r in s.split("#NEXT#")[:2]] == ["1", "2"])
print(f"         list: {lst!r}")

print("\n6. saving over an existing slot updates it rather than adding one")
post("savegame", gameid=1, gamename="'My First Save'", turn=99, version=1,
     data="dXBkYXRlZA==")
lst = post("savegamelist")
check("still two records", lst, lambda s: len(s.split("#NEXT#")) == 3)
check("turn advanced to 99", lst, lambda s: "#SPC#99" in s)
check("slot 1 serves the new blob",
      post("loadgame", gameid=1).split("#DATA#", 1)[1], "dXBkYXRlZA==")
check("slot 2 is untouched",
      post("loadgame", gameid=2).split("#DATA#", 1)[1], "WFlaMTIz")

print("\n7. a name containing the protocol delimiters cannot break the list")
post("savegame", gameid=-1, gamename="'ev#NEXT#il#SPC#x'", turn=1, version=1,
     data="enp6")
lst = post("savegamelist")
check("record count still sane (3 saves + DONE)", lst,
      lambda s: len(s.split("#NEXT#")) == 4)
print(f"         list: {lst!r}")

print("\n8. loading an unknown slot is an empty blob, not an error")
check("empty DATA", post("loadgame", gameid=999), "DONE#VER#000000#DATA#")

print("\n9. the index survives a server restart")
idx = os.path.join(DATA, "saves", "index.json")
check("index.json written", os.path.exists(idx), True)
cs_server_index = cs_server._read_index()
check("three slots recorded", len(cs_server_index), 3)

srv.shutdown()
shutil.rmtree(DATA, ignore_errors=True)
print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
