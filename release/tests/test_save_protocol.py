"""
Protocol test for the save/load slot handling in cs_server.

Speaks the wire format directly, without the game:

    savegame      -> DONE
    savegamelist  -> <gameid>#SPC#<name>#SPC#<turn>#NEXT#...#NEXT#DONE
                     bare DONE for an empty list
    loadgame      -> DONE#VER#<6>#DATA#<blob>
"""
import http.client
import json
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

print("\n10. the civ name survives a server restart")
check("an unnamed civ is the default",
      post("listcivnames", userid=1), "DemoEmpire#SPC#0#NEXT#DONE")
post("uploadcivname", userid=1, passhash="'abcdef'", civname="'Vega Union'")
check("the new name is listed",
      post("listcivnames", userid=1), "Vega Union#SPC#0#NEXT#DONE")
check("civs.json written", os.path.exists(os.path.join(DATA, "civs.json")), True)
cs_server._civ_state = None             # what a fresh process starts with
check("and read back after a restart",
      post("listcivnames", userid=1), "Vega Union#SPC#0#NEXT#DONE")
check("another user still gets the default",
      post("listcivnames", userid=2), "DemoEmpire#SPC#0#NEXT#DONE")

print("\n11. a finished Tutorial is recorded")
done_file = os.path.join(DATA, "tutorial.json")
check("nothing is recorded before it", os.path.exists(done_file), False)
check("the client is answered OK",
      post("passedtutorial", userid=0, passhash="'abcdef'"), "OK")
check("tutorial.json says so",
      json.load(open(done_file, encoding="utf-8")).get("passed"), True)


def raw_post(action, body):
    """A POST whose body is sent as the client sends it, base64 unescaped."""
    c = http.client.HTTPConnection("127.0.0.1", 8899, timeout=5)
    c.request("POST", f"/clientinterface.php?action={action}", body,
              {"Content-Type": "application/x-cosmicsupremacy",
               "Content-Length": str(len(body))})
    out = c.getresponse().read().decode("latin-1")
    c.close()
    return out


print("\n12. the governor library")
GOV = "eJy+rule/s+AA=="        # base64 with '+' and '/', which parse_qs would mangle
check("an empty library is a bare DONE", post("govlist", userid=1), "DONE")
saved = raw_post("savegov", "userid=1&passhash='abcdef'&govid=-1&"
                 f"govname='Core worlds'&version=2&data={GOV}")
check("a new governor (govid -1) is taken", saved, "DONE")
lst = post("govlist", userid=1)
check("and listed as id 1 with its name", lst.startswith("1#SPC#Core worlds#SPC#Saved "),
      True)
check("the list ends in DONE", lst.endswith("#NEXT#DONE"), True)
check("loading it gives version 2 and the data byte for byte",
      post("loadgov", userid=1, govid=1), f"DONE#VER#000002#DATA#{GOV}")
raw_post("savegov", "userid=1&passhash='abcdef'&govid=1&govname='Core worlds'&"
         "version=2&data=bmV3")
check("saving under an existing id overwrites it",
      post("loadgov", userid=1, govid=1), "DONE#VER#000002#DATA#bmV3")
raw_post("savegov", "userid=1&passhash='abcdef'&govid=-1&govname='Mining'&"
         "version=2&data=bWlu")
check("a second new name gets id 2",
      post("govlist", userid=1).count("#NEXT#"), 2)
check("deleting answers DONE", post("delgov", userid=1, govid=1), "DONE")
check("and the list keeps only the other",
      post("govlist", userid=1).startswith("2#SPC#Mining#SPC#"), True)
check("a deleted governor does not load",
      post("loadgov", userid=1, govid=1).startswith("DONE#VER#"), False)

srv.shutdown()
shutil.rmtree(DATA, ignore_errors=True)
print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
