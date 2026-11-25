open Hardcaml_asic_execution
module Producer = Hardcaml_asic.Elaboration.Timing_coverage_tcl
let s x=`String x
let obj x=`Assoc x
let arr x=`List x
let get j k=match j with `Assoc xs -> List.assoc k xs | _ -> assert false
let set j k v=match j with `Assoc xs -> obj ((k,v)::List.remove_assoc k xs) | _ -> assert false
let json=Yojson.Safe.to_string
let sha=Identity_json.sha256_bytes
let names=["no_input_delay";"no_output_delay";"multiple_clock";"no_clock";
           "unconstrained_endpoints";"loops";"generated_clocks"]
let group=obj ["message",s "Native diagnostic, not an English coverage inference";
  "details",arr [s "bus[0]";s "a\"b\\c\nnext"]]
let cats=List.mapi (fun i name -> obj ["name",s name;"group_count",`Int (if i=0 then 1 else 0);
  "groups",arr (if i=0 then [group] else [])]) names
let data=obj ["schema",`Int 2;"contract",s Producer.contract;"profile",s Producer.profile;
  "complete",`Bool true;"categories",arr cats;"endpoint_count",`Int 2;
  "endpoints",arr [s "u0/D";s "u1/D"]]
let checks_for categories =
  "HC_ASIC_NATIVE_CHECKS_V2 opensta-2.7-native-state-v2\n"^
  String.concat "" (List.map (fun category ->
    let name=match get category "name" with `String x -> x | _ -> assert false in
    let groups=match get category "groups" with `List xs -> xs | _ -> assert false in
    "HC_ASIC_NATIVE_CATEGORY_BEGIN "^name^"\n"^
    String.concat "" (List.map (fun group ->
      let message=match get group "message" with `String x -> x | _ -> assert false in
      let details=match get group "details" with `List xs -> xs | _ -> assert false in
      message^"\n"^String.concat "" (List.map (function `String x -> "  "^x^"\n" | _ -> assert false) details)) groups)^
    "HC_ASIC_NATIVE_CATEGORY_END "^name^"\n") categories)^
  "HC_ASIC_NATIVE_CHECKS_V2_COMPLETE\n"
let checks=checks_for cats
let endpoints="\"u0/D\"\n\"u1/D\"\nHC_ASIC_NATIVE_ENDPOINTS_COMPLETE\n"
let resolved=[obj ["source",s "ena";"targets",arr [s "u0/D"]];
              obj ["source",s "rst_n";"targets",arr [s "u0/RESET_B";s "u1/RESET_B"]]]
let capture="set_false_path -from [get_ports {ena}] -to [get_pins {u0/D}]\n"
let reset="set_false_path -from [get_ports {rst_n}] -to [list [get_pins {u0/RESET_B}] [get_pins {u1/RESET_B}]]\n"
let native_sdc="# native fixture, NOT native acceptance\ncreate_clock -name clk -period 20 [get_ports {clk}]\n"^capture^reset
let with_top top state=if String.starts_with ~prefix:"current_design " state then state else "current_design "^top^"\n"^state
let inspect ?(top="top") ?(design=true) ?(resolved=resolved) ?(state=native_sdc) ?(data=data) ?(checks=checks) ?(endpoints=endpoints) () =
  Timing_native_coverage.inspect ~top ~resolved ~native_sdc:(if design then with_top top state else state) ~native_data:(json data)
    ~native_checks:checks ~native_endpoints:endpoints
let tests=ref 0
let ok label result=incr tests;match result with Ok j -> j | Error e -> failwith (label^": "^e)
let bad_text label expected result=
  incr tests;match result with
  | Error e when let n=String.length expected in
      let rec loop i=i+n<=String.length e && (String.sub e i n=expected || loop (i+1)) in loop 0 -> ()
  | Error e -> failwith (label^": wrong diagnostic: "^e)
  | Ok _ -> failwith ("accepted "^label)
let write p raw=let oc=open_out_bin p in Fun.protect ~finally:(fun () -> close_out_noerr oc)
  (fun () -> output_string oc raw)
let read p=let ic=open_in_bin p in Fun.protect ~finally:(fun () -> close_in_noerr ic)
  (fun () -> really_input_string ic (in_channel_length ic))
let with_files files f=
  let dir=Filename.temp_file "native-timing-" "" in Sys.remove dir;Unix.mkdir dir 0o700;
  Fun.protect ~finally:(fun () -> Array.iter (fun p -> Sys.remove (Filename.concat dir p)) (Sys.readdir dir);Unix.rmdir dir)
    (fun () -> List.iter (fun (p,raw) -> write (Filename.concat dir p) raw) files;f dir)
let event kind fields=obj (["schema_version",`Int 1;"event",s kind]@fields)
let counts=["stage_count",`Int 2;"exception_count",`Int 1]
let stage id cell=event "stage" ["id",s id;"anchor",s ("hc_pt7_"^id);"cell",s cell;
  "master",s "sg13cmos5l_dfrbpq_1";"q_pin",s (cell^"/Q");"data_pin",s (cell^"/D");"async_pin",s (cell^"/RESET_B")]
let scope=["id",s "capture";"kind",s "async_capture";"source",s "ena";"targets",arr [s "u0/D"]]
let events=[event "begin" counts;stage "rx0" "u0";stage "rx1" "u1";
  event "resolved" scope;event "validated" counts;event "applied" scope;event "complete" counts]
let wire=String.concat "" (List.map (fun j -> "HC_ASIC_TIMING_V1\t"^json j^"\n") events)
let resign manifest=match manifest with `Assoc xs ->
  let payload=obj (List.remove_assoc "identity" xs) in set payload "identity" (s (sha (json payload)))
  | _ -> assert false
let fixture ?(schema=2) ?(historical=true) ?(marker=true) ?(native_state=capture)
    ?(native_data=data) ?(producer_bytes=Producer.render ()) ?(producer_hash=None) ?(native_contract=true)
    ?(legacy=false) ?(native_design=true) ?(log_transform=Fun.id) () =
  let manifest,emitted_sdc=Bundle_fixture.make () in
  let sdc=if historical then "# retained historical serializer, intentionally not current Tcl\n" else emitted_sdc in
  let files=match get manifest "files" with `List xs -> List.map (fun file ->
    if get file "path"=s "constraints/top.sdc" then set file "sha256" (s (sha sdc))
    else if get file "path"=s Producer.path then set file "sha256" (s (Option.value producer_hash ~default:(sha producer_bytes))) else file) xs
    | _ -> assert false in
  let manifest=set manifest "files" (arr files) in
  let manifest=if legacy then set manifest "timing_native_coverage"
    (set (set (Producer.manifest ()) "schema" (`Int 1)) "profile" (s "opensta-2.7-native-state-v1")) else manifest in
  let manifest=if native_contract then manifest else (match manifest with `Assoc xs -> obj (List.remove_assoc "timing_native_coverage" xs) | _ -> assert false) in
  let manifest=resign manifest in
  let inputs=["manifest",json manifest;"sdc",sdc;"netlist","synthetic netlist\n";
    "liberty","synthetic Liberty\n";"driver","# synthetic driver\n"]@
    (if schema=2 then ["native_producer",producer_bytes] else []) in
  let source (role,raw)=obj ["role",s role;"path",s (role^".txt");"sha256",s (sha raw)] in
  let context=["build_identity",get manifest "identity";"audit_id",s "pt7c-synthetic";
    "stage",s "synthesized_netlist";"producer",obj ["tool",s "OpenSTA";"version",s "synthetic-only"];
    "corner",s "fixture"] in
  let receipt=obj (["schema",`Int 1]@context@["inputs",arr (List.map source inputs)]) in
  let process=obj ["schema",`Int 1;"receipt_sha256",s (sha (json receipt));"exit_code",`Int 0;
    "argv",arr [s "synthetic";s "driver.txt"]] in
  let log=log_transform ("PT7_AUDIT_RECEIPT "^json receipt^"\n"^wire^
    (if marker then (if legacy then "HC_ASIC_NATIVE_V1_COMPLETE\n" else "HC_ASIC_NATIVE_V2_COMPLETE\n") else "")^"PT7_AUDIT_COMPLETE\n") in
  let native_data=if legacy then set (set native_data "schema" (`Int 1)) "profile" (s "opensta-2.7-native-state-v1") else native_data in
  let contents=inputs@["receipt",json receipt;"process_result",json process;"producer_version","synthetic-only\n";
    "log",log;"resolution",wire;"exceptions","native raw report\n";"unconstrained","native raw report\n";
    "downstream_min","slack -0.044244 VIOLATED\n";"downstream_max","slack +19.432287 MET\n"]@
    (if schema=2 then ["native_sdc",(if native_design then with_top (match get manifest "top_module" with `String x -> x | _ -> assert false) native_state else native_state);"native_data",json native_data;
       "native_checks",checks;"native_endpoints",endpoints] else []) in
  let audit=obj (["schema",`Int schema;"kind",s "hardcaml-asic.timing-intent-audit";
    "scope",s "standalone_synthesized_netlist_sta"]@context@["sources",arr (List.map source contents)]) in
  ("audit.json",json audit)::List.map (fun (role,raw) -> role^".txt",raw) contents
let audit ?schema ?historical ?marker ?native_state ?native_data ?producer_bytes ?producer_hash ?native_contract ?legacy ?native_design ?log_transform () =
  with_files (fixture ?schema ?historical ?marker ?native_state ?native_data ?producer_bytes ?producer_hash ?native_contract ?legacy ?native_design ?log_transform ())
    (fun directory -> Timing_intent_evidence.inspect ~directory ())
let run ()=
  let newline_cases=match Yojson.Safe.from_string (read "native-newline-cases.json") with
    | `List xs -> xs | _ -> assert false in
  let one=[obj ["source",s "ena";"targets",arr [s "u0/D"]]] in
  List.iter (fun case ->
    let str key=match get case key with `String x -> x | _ -> assert false in
    let label=str "name" and state=str "state" and expected=str "reader" in
    let prefix="current_design top" in
    assert (String.starts_with ~prefix state);
    let audit_state="current_design tt_um_pt7_evidence_fixture"^
      String.sub state (String.length prefix) (String.length state-String.length prefix) in
    let pure=inspect ~design:false ~resolved:one ~state () in
    let complete=audit ~native_design:false ~native_state:audit_state () in
    if expected="accept" then (
      ignore (ok (label^" pure") pure);ignore (ok (label^" schema2") complete))
    else (
      bad_text (label^" pure") expected pure;
      bad_text (label^" schema2") expected complete)) newline_cases;
  let hidden=read "native-continued-comment-excerpt.sdc" in
  let one=[obj ["source",s "ena";"targets",arr [s "u0/D"]]] in
  List.iter (fun (label,state) ->
    bad_text label "continued native SDC comment" (inspect ~resolved:one ~state ())) [
    "continued comment hides required scope",hidden;
    "continued indented comment","  "^hidden;
    "continued semicolon comment",";"^hidden;
    "continued comment hides extra masking",native_sdc^"# ignored \\\nset_disable_timing [get_cells u0]\n" ];
  bad_text "schema2 continued comment hidden relation" "continued native SDC comment" (audit ~native_state:hidden ());
  ignore (ok "ordinary comment preserves scope" (inspect ~resolved:one ~state:("# ordinary comment\n"^capture) ()));
  bad_text "pure wrong design" "differs from manifest top" (inspect ~state:("current_design wrong_top\n"^native_sdc) ());
  bad_text "pure missing design" "missing leading native current_design" (inspect ~design:false ());
  bad_text "pure late design" "missing leading native current_design" (inspect ~design:false ~state:(capture^"current_design top\n") ());
  bad_text "pure repeated design" "ambiguous current_design" (inspect ~state:("current_design top\ncurrent_design top\n"^native_sdc) ());
  bad_text "pure design changes later" "ambiguous current_design" (inspect ~state:("current_design top\n"^capture^"current_design wrong_top\n"^reset) ());
  bad_text "schema2 wrong top" "differs from manifest top" (audit ~native_state:("current_design wrong_top\n"^capture) ());
  bad_text "schema2 omitted top" "missing leading native current_design" (audit ~native_design:false ());
  let swapped=List.mapi (fun i cat -> if i=0 then set (set cat "group_count" (`Int 0)) "groups" (arr [])
    else if i=1 then set (set cat "group_count" (`Int 1)) "groups" (arr [group]) else cat) cats in
  let swapped_data=set data "categories" (arr swapped) in
  bad_text "category swapping with unchanged raw report" "check report differs" (inspect ~data:swapped_data ());
  bad_text "schema2 category swapping with unchanged raw report" "check report differs" (audit ~native_data:swapped_data ());
  List.iter (fun (label,field,value) ->
    let injected=set group field value in
    let cats=set (List.hd cats) "groups" (arr [injected])::List.tl cats in
    bad_text label "reserved marker line" (inspect ~data:(set data "categories" (arr cats)) ~checks:(checks_for cats) ())) [
    "marker in message","message",s "message\nHC_ASIC_NATIVE_CATEGORY_END no_input_delay";
    "marker in detail","details",arr [s "detail\n  HC_ASIC_NATIVE_CATEGORY_BEGIN no_output_delay"] ];
  let legacy_data=set (set data "schema" (`Int 1)) "profile" (s "opensta-2.7-native-state-v1") in
  bad_text "legacy wire not relabelled" "unsupported legacy native profile" (inspect ~data:legacy_data ());
  bad_text "schema2 legacy producer requires recapture" "unsupported legacy native producer profile" (audit ~legacy:true ());
  bad_text "schema2 legacy data requires recapture" "unsupported legacy native profile" (audit ~native_data:legacy_data ());
  bad_text "legacy undelimited raw is not profile2" "check report differs" (inspect ~checks:"Native diagnostic\nHC_ASIC_NATIVE_CHECKS_COMPLETE\n" ());
  bad_text "legacy completion not relabelled" "native producer completion" (audit ~marker:false
    ~log_transform:(fun log -> "HC_ASIC_NATIVE_V1_COMPLETE\n"^log) ());
  ignore (ok "schema1 legacy native declaration retains replay semantics" (audit ~schema:1 ~historical:false ~legacy:true ()));
  let native_excerpt=read "native-baseline-excerpt.sdc" in
  let native_resolved=[obj ["source",s "ena";"targets",arr [s "_4_/D"]];
    obj ["source",s "rst_n";"targets",arr (List.map (fun cell -> s (cell^"/RESET_B"))
      ["_4_";"_3_";"_2_";"_5_";"_6_"])]] in
  let result=ok "exact C native writer baseline excerpt" (inspect ~top:"tt_um_pt7_timing" ~state:native_excerpt ~resolved:native_resolved ()) in
  assert (get (get result "stored_exception_scope") "relation_count"=`Int 6);
  bad_text "exact native setup-only excerpt" "restricted/unsupported false-path option"
    (inspect ~top:"tt_um_pt7_timing" ~state:(native_excerpt^read "native-setup-only-excerpt.sdc") ~resolved:native_resolved ());
  bad_text "exact native masking excerpt" "unsupported native SDC command: set_disable_timing"
    (inspect ~top:"tt_um_pt7_timing" ~state:(native_excerpt^read "native-masking-excerpt.sdc") ~resolved:native_resolved ());
  List.iter (fun (label,addition,diagnostic) ->
    bad_text label diagnostic (inspect ~top:"tt_um_pt7_timing" ~state:(native_excerpt^addition) ~resolved:native_resolved ())) [
    "continued hold restriction", "set_false_path\\\n -hold\\\n -from [get_ports ena]\\\n -to [get_pins _4_/D]\n", "restricted";
    "continued broad target", "set_false_path\\\n -from [get_ports ena]\\\n -to [get_pins {_*/D}]\n", "broad";
    "continued extra target", "set_false_path\\\n -from [get_ports ena]\\\n -to [get_pins {_3_/D}]\n", "extra exception";
    "continued disable", "set_disable_timing\\\n -from {CLK} -to {Q} [get_cells {_4_}]\n", "unsupported native SDC command";
    "continued case", "set_case_analysis\\\n 0 [get_ports rst_n]\n", "unsupported native SDC command";
    "derate zero", "set_timing_derate -early 0\n", "derate factor";
    "derate negative", "set_timing_derate -early -0.95\n", "derate factor";
    "derate NaN", "set_timing_derate -late NaN\n", "derate factor";
    "derate invalid decimal", "set_timing_derate -early .\n", "derate factor";
    "derate infinity", "set_timing_derate -late Inf\n", "derate factor";
    "derate overflow", "set_timing_derate -late "^String.make 400 '9'^"\n", "derate factor";
    "derate early wrong direction", "set_timing_derate -early 1.05\n", "derate factor/range";
    "derate late wrong direction", "set_timing_derate -late 0.95\n", "derate factor/range";
    "derate missing value", "set_timing_derate -early\n", "derate scope/options";
    "derate extra value", "set_timing_derate -early 0.95 1\n", "derate scope/options";
    "derate both directions", "set_timing_derate -early -late 1\n", "derate scope/options";
    "derate unknown option", "set_timing_derate -bogus 1\n", "derate scope/options";
    "derate incremental", "set_timing_derate -increment -early 0.95\n", "derate scope/options";
    "derate scoped object", "set_timing_derate -early 0.95 [get_cells {_4_}]\n", "derate scope/options";
    "derate check scoped", "set_timing_derate -cell_check -early 0.95\n", "derate scope/options";
    "derate script value", "set_timing_derate -early [set_disable_timing [get_cells {_4_}]]\n", "derate scope/options";
    "current design query arguments", "set_max_fanout 10 [current_design other]\n", "unsupported base query";
    "current design nested script", "set_max_fanout 10 [current_design [set_case_analysis 0 [get_ports rst_n]]]\n", "unsupported base query";
    "current design query semicolon", "set_max_fanout 10 [current_design; set_disable_timing [get_cells {_4_}]]\n", "multiple commands in query";
    "current design query newline", "set_max_fanout 10 [current_design\nset_disable_timing [get_cells {_4_}]]\n", "multiple commands in query";
    "object query newline injection", "set_load 1 [get_ports ena\nset_disable_timing [get_cells {_4_}]]\n", "multiple commands in query";
    "current design dynamic selection", "current_design [get_cells {_4_}]\n", "ambiguous current_design selection";
    "current design extra arguments", "current_design top extra\n", "ambiguous current_design selection" ];
  ignore (ok "continuations between all complete bare/query words" (inspect
    ~state:"set_false_path\\\n-from\\\n[get_ports ena]\\\n-to\\\n[get_pins u0/D]\n"
    ~resolved:[obj ["source",s "ena";"targets",arr [s "u0/D"]]] ()));
  bad_text "continuation is not token concatenation" "unsupported native SDC command"
    (inspect ~state:"set_false_\\\npath -from [get_ports ena] -to [get_pins u0/D]\n" ());
  bad_text "quoted continuation stays within one word" "restricted/unsupported false-path option"
    (inspect ~state:"set_false_path \"-fr\\\nom\" [get_ports ena] -to [get_pins u0/D]\n" ());
  bad_text "escaped space is not a continuation" "unsupported native SDC command"
    (inspect ~state:"set_false_path\\ -from [get_ports ena] -to [get_pins u0/D]\n" ());
  bad_text "escaped backslash is not a continuation" "unsupported native SDC command"
    (inspect ~state:"set_false_path\\\\\n -from [get_ports ena] -to [get_pins u0/D]\n" ());
  ignore (ok "continued global derates and current_design query" (inspect ~top:"tt_um_pt7_timing" ~state:(native_excerpt^
    "set_timing_derate\\\n -early\\\n 1.000000000000\nset_timing_derate -late 1.000000000000\nset_max_fanout\\\n 10\\\n [current_design]\n") ~resolved:native_resolved ()));
  let result=ok "exact" (inspect ()) in
  assert (get (get result "stored_exception_scope") "relation_count"=`Int 3);
  let manifest,_=Bundle_fixture.make () in
  assert (get manifest "timing_native_coverage"=Producer.manifest ());
  let members=match get manifest "files" with `List xs -> xs | _ -> assert false in
  let member=List.find (fun file -> get file "path"=s Producer.path) members in
  assert (get member "sha256"=s (sha (Producer.render ())));
  let manifest_again,_=Bundle_fixture.make () in
  incr tests;assert (get manifest "identity"=get manifest_again "identity");
  let rich_cats=List.mapi (fun i name -> obj ["name",s name;"group_count",`Int 1;
    "groups",arr [obj ["message",s ("native "^string_of_int i);"details",arr [s ("exact/"^name);s "not classified by English"]]]]) names in
  let rich_report=checks_for rich_cats in
  let rich=ok "all seven complete groups" (inspect ~data:(set data "categories" (arr rich_cats)) ~checks:rich_report ()) in
  assert (get (get rich "diagnostics") "categories"=arr rich_cats);
  let empty_cats=List.map (fun name -> obj ["name",s name;"group_count",`Int 0;"groups",arr []]) names in
  ignore (ok "explicit empty complete reports" (inspect
    ~data:(set (set (set data "categories" (arr empty_cats)) "endpoints" (arr [])) "endpoint_count" (`Int 0))
    ~checks:(checks_for empty_cats) ~endpoints:"HC_ASIC_NATIVE_ENDPOINTS_COMPLETE\n" ()));
  ignore (ok "split" (inspect ~state:(capture^
    "set_false_path -to [get_pins {u1/RESET_B}] -from [get_ports rst_n]\n"^
    "set_false_path -from [get_ports rst_n] -to [get_pins u0/RESET_B]\n") ()));
  ignore (ok "merged selector" (inspect ~state:(capture^
    "set_false_path -from [get_ports rst_n] -to [get_pins {u0/RESET_B u1/RESET_B}]\n") ()));
  ignore (ok "duplicate equivalent relation" (inspect ~state:(native_sdc^capture) ()));
  ignore (ok "merged Cartesian" (inspect ~resolved:[obj ["source",s "a";"targets",arr [s "u0/D";s "u1/D"]];
    obj ["source",s "b";"targets",arr [s "u0/D";s "u1/D"]]]
    ~state:"set_false_path -from [list [get_ports a] [get_ports b]] -to [get_pins {u0/D u1/D}]\n" ()));
  ignore (ok "bit literal and continuation" (inspect ~resolved:[obj ["source",s "ui_in[0]";"targets",arr [s "u0/D"]]]
    ~state:"set_false_path \\\n  -from [get_ports {ui_in[0]}] \\\n -to [get_pins {u0/D}]\n" ()));
  List.iter (fun (label,state,diagnostic) -> bad_text label diagnostic (inspect ~state ())) [
    "missing",capture,"missing exception";
    "extra",native_sdc^"set_false_path -from [get_ports ena] -to [get_pins u1/D]\n","extra exception";
    "broad pin",capture^"set_false_path -from [get_ports rst_n] -to [get_pins */RESET_B]\n","broad";
    "broad cell",capture^"set_false_path -from [get_ports rst_n] -to [get_cells u0]\n","unsupported";
    "setup only","set_false_path -setup -from [get_ports ena] -to [get_pins u0/D]\n","restricted";
    "hold only","set_false_path -hold -from [get_ports ena] -to [get_pins u0/D]\n","restricted";
    "split setup hold still unsupported","set_false_path -setup -from [get_ports ena] -to [get_pins u0/D]\nset_false_path -hold -from [get_ports ena] -to [get_pins u0/D]\n","restricted";
    "rise only","set_false_path -rise_from [get_ports ena] -to [get_pins u0/D]\n","restricted";
    "fall only","set_false_path -from [get_ports ena] -fall_to [get_pins u0/D]\n","restricted";
    "through","set_false_path -from [get_ports ena] -through [get_pins u0/Q] -to [get_pins u0/D]\n","restricted";
    "duplicate option",capture^"set_false_path -from [get_ports rst_n] -from [get_ports ena] -to [get_pins u0/D]\n","restricted";
    "disable",native_sdc^"set_disable_timing [get_cells u0]\n","unsupported native SDC command";
    "case",native_sdc^"set_case_analysis 0 [get_ports rst_n]\n","unsupported native SDC command";
    "clock groups",native_sdc^"set_clock_groups -asynchronous -group [get_clocks clk]\n","unsupported";
    "min delay",native_sdc^"set_min_delay 1 -to [get_pins u0/D]\n","unsupported";
    "hidden script",native_sdc^"set_load [exec touch /tmp/forbidden] [get_ports x]\n","unsupported base query";
    "query injection",native_sdc^"set_load [get_ports x; exec bad] 1\n","multiple commands";
    "variable","set_false_path -from $sources -to [get_pins u0/D]\n","substitution";
    "truncated query",capture^"set_false_path -from [get_ports rst_n\\\n","unterminated";
    "compound",capture^"set_load [get_ports ena]suffix 1\n","compound";
    "unknown",native_sdc^"unknown_command\n","unsupported" ];
  bad_text "truncated serialization" "final newline" (inspect ~state:(String.trim native_sdc) ());
  bad_text "NUL comment" "NUL" (inspect ~state:(native_sdc^"# \000\n") ());
  bad_text "SDC token budget" "token" (inspect ~state:(native_sdc^"set_load "^String.concat " " (List.init 262145 (fun _ -> "0"))^"\n") ());
  let fanout=String.concat " " (List.init 257 (fun i -> "u"^string_of_int i^"/D")) in
  let sources=String.concat " " (List.init 256 (fun i -> "i"^string_of_int i)) in
  bad_text "relation expansion budget" "expanded relation" (inspect ~state:(
    "set_false_path -from [get_ports {"^sources^"}] -to [get_pins {"^fanout^"}]\n") ());
  List.iter (fun (label,data,expected) -> bad_text label expected (inspect ~data ())) [
    "incomplete",set data "complete" (`Bool false),"incomplete";
    "missing category",set data "categories" (arr (List.tl cats)),"categories";
    "duplicate category",set data "categories" (arr (List.hd cats::cats)),"categories";
    "category count",set data "categories" (arr (set (List.hd cats) "group_count" (`Int 0)::List.tl cats)),"count";
    "endpoint count",set data "endpoint_count" (`Int 0),"count";
    "duplicate endpoint",set data "endpoints" (arr [s "u0/D";s "u0/D"]),"duplicate";
    "reordered endpoint",set data "endpoints" (arr [s "u1/D";s "u0/D"]),"unsorted" ];
  bad_text "missing raw checks" "differs" (inspect ~checks:"HC_ASIC_NATIVE_CHECKS_COMPLETE\n" ());
  bad_text "truncated endpoints" "JSON" (inspect ~endpoints:"\"u0/D\"\n" ());
  bad_text "blank-line amplification" "line budget" (inspect ~endpoints:(String.make 1000000 '\n') ());
  bad_text "native string budget" "bounded string" (inspect ~data:(set data "endpoints" (arr [s (String.make 65537 'x');s "z"])) ());
  let duplicate="{\"schema\":1,"^String.sub (json data) 1 (String.length (json data)-1) in
  bad_text "duplicate native JSON" "duplicate key" (Timing_native_coverage.inspect
    ~top:"top" ~resolved ~native_sdc:(with_top "top" native_sdc) ~native_data:duplicate ~native_checks:checks ~native_endpoints:endpoints);
  bad_text "nonfinite native JSON" "malformed" (Timing_native_coverage.inspect
    ~top:"top" ~resolved ~native_sdc:(with_top "top" native_sdc) ~native_data:"{\"schema\":NaN}" ~native_checks:checks ~native_endpoints:endpoints);
  bad_text "native JSON depth budget" "structural budget" (Timing_native_coverage.inspect
    ~top:"top" ~resolved ~native_sdc:(with_top "top" native_sdc) ~native_data:(String.make 17 '['^String.make 17 ']') ~native_checks:checks ~native_endpoints:endpoints);
  bad_text "budget" "byte budget" (inspect ~state:(String.make (8*1024*1024+1) ' ') ());
  let retained="# retained older contract-compatible producer, no current renderer equality\n"^Producer.render () in
  let result=ok "schema2 historical retained producer and SDC" (audit ~producer_bytes:retained ()) in
  assert (get result "verdict"=s "unavailable");
  assert (get (get result "native_coverage") "status"=s "interpreted_supported_dimensions");
  ignore (ok "schema1 current replay" (audit ~schema:1 ~historical:false ()));
  bad_text "schema1 still replayed" "serializer replay" (audit ~schema:1 ());
  bad_text "producer member binding" "producer differs" (audit ~producer_hash:(Some (String.make 64 '0')) ());
  bad_text "schema2 contract required" "requires" (audit ~native_contract:false ());
  bad_text "missing native completion" "native producer completion" (audit ~marker:false ());
  bad_text "duplicate native completion" "duplicate native completion" (audit ~log_transform:(fun log -> log^"HC_ASIC_NATIVE_V2_COMPLETE\n") ());
  bad_text "early native completion" "reordered native producer completion" (audit ~marker:false
    ~log_transform:(fun log -> "HC_ASIC_NATIVE_V2_COMPLETE\n"^log) ());
  bad_text "schema2 wrong native scope" "extra exception" (audit ~native_state:(capture^reset) ());
  with_files (fixture ()) (fun directory ->
    write (directory^"/native_data.txt") "{}";
    bad_text "native hash" "source hash mismatch" (Timing_intent_evidence.inspect ~directory ()));
  let files=fixture () in
  let audit_json=Yojson.Safe.from_string (List.assoc "audit.json" files) in
  let sources=match get audit_json "sources" with `List xs -> xs | _ -> assert false in
  let audit_json=set audit_json "sources" (arr (List.filter (fun source -> get source "role"<>s "native_checks") sources)) in
  with_files (("audit.json",json audit_json)::List.remove_assoc "audit.json" files) (fun directory ->
    bad_text "missing native report" "native_checks" (Timing_intent_evidence.inspect ~directory ()));
  Printf.printf "Native timing coverage: %d pure checks passed (no EDA)\n" !tests
let ()=if Array.length Sys.argv=2 && Sys.argv.(1)="--producer" then print_string (Producer.render ()) else run ()
