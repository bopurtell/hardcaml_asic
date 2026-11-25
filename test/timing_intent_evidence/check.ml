open Hardcaml_asic_execution
let s x = `String x
let obj xs = `Assoc xs
let arr xs = `List xs
let get j k = match j with `Assoc xs -> List.assoc k xs | _ -> assert false
let set j k v = match j with `Assoc xs -> obj (List.map (fun (key,x) -> key,(if key=k then v else x)) xs) | _ -> assert false
let json = Yojson.Safe.to_string
let sha = Identity_json.sha256_bytes
let write p raw = let oc=open_out_bin p in Fun.protect ~finally:(fun () -> close_out oc) (fun () -> output_string oc raw)
let read p = let ic=open_in_bin p in Fun.protect ~finally:(fun () -> close_in ic) (fun () -> really_input_string ic (in_channel_length ic))
let stage id index = obj ["id",s id;"chain",s "rx";"index",`Int index;"anchor",s ("hc_pt7_"^id);"preservation",s "yosys_keep_1"]
let ex = obj ["id",s "capture";"kind",s "async_capture";"reason",s "external asynchronous capture";
  "source",obj ["direction",s "input";"port",s "ena";"bit",`Null];"targets",arr [s "rx0"]]
let intent = obj ["contract",s "hardcaml-asic.timing-intent";"schema_version",`Int 1;
  "resolution_policy",s "scalar-q-driver-cmos5l-v1";"stages",arr [stage "rx0" 0;stage "rx1" 1];
  "exceptions",arr [ex];"sdc",s "constraints/top.sdc"]
let real_bundle=lazy (Bundle_fixture.make ())
let resign manifest =
  let payload=match manifest with `Assoc xs -> obj (List.remove_assoc "identity" xs) | _ -> assert false in
  match payload with `Assoc xs -> obj (("identity",s (sha (json payload)))::xs) | _ -> assert false
let reseal manifest =
  let config=get manifest "flow_configuration" in
  let files=match get manifest "files" with `List xs -> List.map (fun file ->
    if get file "path"=s "src/config.json" then set file "sha256" (s (sha (Yojson.Safe.pretty_to_string config^"\n"))) else file) xs | _ -> assert false in
  resign (set manifest "files" (arr files))
let manifest intent sdc =
  let template,_=Lazy.force real_bundle in
  let files=match get template "files" with `List xs -> List.map (fun file ->
    if get file "path"=s "constraints/top.sdc" then set file "sha256" (s (sha sdc)) else file) xs | _ -> assert false in
  resign (set (set template "timing_intent" intent) "files" (arr files))
let event kind fields=obj (["schema_version",`Int 1;"event",s kind]@fields)
let counts=["stage_count",`Int 2;"exception_count",`Int 1]
let mapped id cell=event "stage" ["id",s id;"anchor",s ("hc_pt7_"^id);"cell",s cell;
  "master",s "sg13cmos5l_dfrbpq_1";"q_pin",s (cell^"/Q");"data_pin",s (cell^"/D");"async_pin",s (cell^"/RESET_B")]
let endpoint=["id",s "capture";"kind",s "async_capture";"source",s "ena";"targets",arr [s "u0/D"]]
let events=[event "begin" counts;mapped "rx0" "u0";mapped "rx1" "u1";
  event "resolved" endpoint;event "validated" counts;event "applied" endpoint;event "complete" counts]
let wire events=String.concat "" (List.map (fun e -> "HC_ASIC_TIMING_V1\t"^json e^"\n") events)
let producer=obj ["tool",s "OpenSTA";"version",s "synthetic-test-only"]
let generated_sdc intent events =
  let module T = Hardcaml_asic.Timing in
  let t0=T.Stage.create ~id:"rx0" ~chain:"rx" ~index:0 in
  let t1=T.Stage.create ~id:"rx1" ~chain:"rx" ~index:1 in
  let stage t id =
    let async=List.exists (fun e -> get e "event"=s "stage" && get e "id"=s id && get e "async_pin"<>`Null) events in
    t,"hc_pt7_"^id,async in
  let exceptions=match get intent "exceptions" with
    | `List xs -> List.map (fun ex ->
      let src=get ex "source" in
      let p=T.Port.input (match get src "port" with `String p -> p | _ -> assert false) in
      let source=match get src "bit" with `Null -> p | `Int n -> T.Port.bit p n | _ -> assert false in
      let id=match get ex "id" with `String x -> x | _ -> assert false in
      let reason=match get ex "reason" with `String x -> x | _ -> assert false in
      if get ex "kind"=s "async_capture" then T.Exception.async_capture ~id ~reason ~source ~first_stage:t0
      else T.Exception.async_reset ~id ~reason ~source ~targets:[t0;t1]) xs
    | _ -> assert false in
  "# Synthetic trace fixture; actual library-generated timing Tcl.\n"^
  Hardcaml_asic.Elaboration.Timing_tcl.render ~stages:[stage t0 "rx0";stage t1 "rx1"] ~exceptions
let fixture ?(intent=intent) ?(events=events) ?(completion=true) ?(prefix="") ?resolution ?(exit_code=0) ?(version="synthetic-test-only\n") ?sdc ?manifest_override ?(receipt_map=Fun.id) () =
  let sdc=Option.value sdc ~default:(generated_sdc intent events) in
  let manifest=Option.value manifest_override ~default:(manifest intent sdc) in
  let inputs=["manifest",json manifest;"sdc",sdc;"netlist","// synthetic mapped-netlist fixture\n";
    "liberty","/* synthetic library fixture */\n";"driver","# synthetic driver fixture\n"] in
  let source (role,raw)=obj ["role",s role;"path",s (role^".txt");"sha256",s (sha raw)] in
  let context=["build_identity",get manifest "identity";"audit_id",s "synthetic-audit";
    "stage",s "synthesized_netlist";"producer",producer;"corner",s "synthetic-corner"] in
  let receipt=receipt_map (obj (["schema",`Int 1]@context@["inputs",arr (List.map source inputs)])) in
  let process=obj ["schema",`Int 1;"receipt_sha256",s (sha (json receipt));"exit_code",`Int exit_code;"argv",arr [s "synthetic-sta";s "driver.txt"]] in
  let block=wire events in
  let log=prefix^"PT7_AUDIT_RECEIPT "^json receipt^"\n"^block^(if completion then "PT7_AUDIT_COMPLETE\n" else "") in
  let files=inputs@["receipt",json receipt;"log",log;"resolution",Option.value resolution ~default:block;
    "exceptions","native exception diagnostics fixture\n";"unconstrained","native unconstrained diagnostics fixture\n";
    "downstream_min","native min report fixture\n";"downstream_max","native max report fixture\n";
    "process_result",json process;"producer_version",version] in
  let audit=obj (["schema",`Int 1;"kind",s "hardcaml-asic.timing-intent-audit";
    "scope",s "standalone_synthesized_netlist_sta"]@context@["sources",arr (List.map source files)]) in
  ("audit.json",json audit)::List.map (fun (role,raw) -> role^".txt",raw) files
let with_fixture files f =
  let root=Filename.temp_file "timing-evidence-" "" in
  Sys.remove root;Unix.mkdir root 0o700;
  Fun.protect ~finally:(fun () -> Array.iter (fun p -> Sys.remove (Filename.concat root p)) (Sys.readdir root);Unix.rmdir root)
    (fun () -> List.iter (fun (path,raw) -> write (Filename.concat root path) raw) files;f root)
let checks=ref 0
let expect_error label f =
  incr checks;
  match f () with Error _ -> () | Ok _ -> failwith ("accepted invalid evidence: "^label)
let negative label ?(files=fixture ()) mutate = with_fixture files (fun root -> mutate root;
  expect_error label (fun () -> Timing_intent_evidence.inspect ~directory:root ()))
let map_event name f=List.map (fun e -> if get e "event"=s name then f e else e) events
let rewrite_audit root f = let path=root^"/audit.json" in write path (json (f (Yojson.Safe.from_string (read path))))
let positive label files = with_fixture files (fun directory ->
  incr checks;
  match Timing_intent_evidence.inspect ~directory () with
  | Ok j -> assert (get j "verdict"=s "unavailable")
  | Error e -> failwith (label^": "^e))
let real_fixture () = let manifest,sdc=Lazy.force real_bundle in fixture ~manifest_override:manifest ~sdc ()
let () =
  if Array.to_list Sys.argv=[Sys.argv.(0);"--seal-manifest"] then (
    print_endline (json (reseal (Yojson.Safe.from_string (input_line stdin))));exit 0);
  if Array.to_list Sys.argv=[Sys.argv.(0);"--emit-real-fixture"] then (
    print_endline (json (obj (List.map (fun (path,raw) -> path,s raw) (real_fixture ()))));exit 0);
  if Array.to_list Sys.argv=[Sys.argv.(0);"--emit-real-simulation-fixture"] then (
    let manifest,sdc=Bundle_fixture.make ~separate_simulation:true () in
    print_endline (json (obj (List.map (fun (path,raw) -> path,s raw) (fixture ~manifest_override:manifest ~sdc ()))));exit 0);
  positive "real library emitted manifest and complete SDC" (real_fixture ());
  let reverse = function `Assoc xs -> obj (List.rev xs) | _ -> assert false in
  positive "receipt producer object order" (fixture ~receipt_map:(fun r -> set r "producer" (reverse (get r "producer"))) ());
  positive "receipt input object order" (fixture ~receipt_map:(fun r -> match get r "inputs" with
    | `List xs -> set r "inputs" (arr (List.map reverse xs)) | _ -> assert false) ());
  negative "receipt input array reordered" ~files:(fixture ~receipt_map:(fun r -> match get r "inputs" with
    | `List xs -> set r "inputs" (arr (List.rev xs)) | _ -> assert false) ()) (fun _ -> ());
  let real_manifest,real_sdc=Lazy.force real_bundle in
  negative "unsupported real manifest schema" ~files:(fixture ~manifest_override:(resign (set real_manifest "schema_version" (`Int 999))) ~sdc:real_sdc ()) (fun _ -> ());
  let minimal=resign (obj ["files",arr [obj ["path",s "constraints/top.sdc";"sha256",s (sha real_sdc)]];"timing_intent",intent]) in
  negative "minimal self-hash object" ~files:(fixture ~manifest_override:minimal ~sdc:real_sdc ()) (fun _ -> ());
  negative "unsupported minimal self-hash schema999" ~files:(fixture ~manifest_override:(resign (match minimal with `Assoc xs -> obj (("schema_version",`Int 999)::xs) | _ -> assert false)) ~sdc:real_sdc ()) (fun _ -> ());
  negative "altered SDC prefix unchanged bundle identity" ~files:(fixture ~manifest_override:real_manifest
    ~sdc:("set_false_path -from [get_ports {*} ]\n"^real_sdc) ()) (fun _ -> ());
  negative "invalid bundle configuration" ~files:(fixture ~manifest_override:(resign (set real_manifest "flow_configuration" (obj []))) ~sdc:real_sdc ()) (fun _ -> ());
  negative "bracketed error" ~files:(fixture ~prefix:"[ERROR STA-0001] synthetic fatal diagnostic\n" ()) (fun _ -> ());
  negative "ANSI bracketed fatal" ~files:(fixture ~prefix:"\027[31m[FATAL STA-0001] synthetic fatal diagnostic\027[0m\n" ()) (fun _ -> ());
  positive "quoted diagnostic text is not a fatal line" (fixture ~prefix:"[INFO STA-0001] example: [ERROR STA-0001] is documentation\n" ());
  negative "two million blank native lines" ~files:(fixture ~prefix:(String.make 2_000_000 '\n') ()) (fun _ -> ());
  negative "excessive native line length" ~files:(fixture ~prefix:(String.make 65537 'x'^"\n") ()) (fun _ -> ());
  with_fixture (fixture ()) (fun directory ->
    let before=Array.to_list (Sys.readdir directory) |> List.sort String.compare in
    let evidence=match Timing_intent_evidence.inspect ~directory () with Ok j -> j | Error e -> failwith e in
    assert (get evidence "verdict"=s "unavailable");
    assert (get (get evidence "resolved") "status"=s "producer_reported");
    assert (get (get evidence "applied") "status"=s "command_completed");
    assert (get (get evidence "native_coverage") "status"=s "unavailable");
    assert ((Array.to_list (Sys.readdir directory) |> List.sort String.compare)=before);
    incr checks;
    let helper="../../bin/experimental_timing_intent_inspect.exe" in
    let out=directory^"/cli-output" in
    let fd=Unix.openfile out [Unix.O_WRONLY;Unix.O_CREAT] 0o600 in
    let pid=Unix.create_process helper [|helper;"--json";directory|] Unix.stdin fd Unix.stderr in
    Unix.close fd;
    assert (snd (Unix.waitpid [] pid)=Unix.WEXITED 1);
    assert (get (Yojson.Safe.from_string (read out)) "verdict"=s "unavailable");incr checks;
    let public="../../bin/flow_cli.exe" in
    let fd=Unix.openfile out [Unix.O_WRONLY;Unix.O_TRUNC] 0o600 in
    let pid=Unix.create_process public [|public;"timing-intent-inspect";directory;"--json"|] Unix.stdin fd Unix.stderr in
    Unix.close fd;
    assert (snd (Unix.waitpid [] pid)=Unix.WEXITED 1);
    assert (Yojson.Safe.from_string (read out)=evidence);incr checks;
    let fd=Unix.openfile out [Unix.O_WRONLY;Unix.O_TRUNC] 0o600 in
    let pid=Unix.create_process public [|public;"--version";"--json"|] Unix.stdin fd Unix.stderr in
    Unix.close fd;
    assert (snd (Unix.waitpid [] pid)=Unix.WEXITED 0);
    let capabilities=get (get (Yojson.Safe.from_string (read out)) "cli_contract") "capabilities" in
    assert (get (get capabilities "timing_intent_evidence1") "native_path_effect_coverage"=s "unavailable");incr checks);
  let reset=set ex "kind" (s "async_reset") |> fun e -> set e "targets" (arr [s "rx0";s "rx1"]) in
  let reset_events=List.map (fun e -> if List.mem (get e "event") [s "resolved";s "applied"] then
    set (set e "kind" (s "async_reset")) "targets" (arr [s "u0/RESET_B";s "u1/RESET_B"]) else e) events in
  positive "enumerated async reset" (fixture ~intent:(set intent "exceptions" (arr [reset])) ~events:reset_events ());
  let bit_ex=set ex "source" (obj ["direction",s "input";"port",s "bus";"bit",`Int 3]) in
  let bit_events=List.map (fun e -> if List.mem (get e "event") [s "resolved";s "applied"] then set e "source" (s "bus[3]") else e) events in
  positive "exact bus bit" (fixture ~intent:(set intent "exceptions" (arr [bit_ex])) ~events:bit_events ());
  positive "data-only stage" (fixture ~events:(map_event "stage" (fun e -> set e "async_pin" `Null)) ());
  negative "source hash" (fun root -> write (root^"/netlist.txt") "changed\n");
  negative "missing report" (fun root -> Sys.remove (root^"/unconstrained.txt"));
  negative "empty report" (fun root -> write (root^"/exceptions.txt") "");
  negative "symlink member" (fun root -> Sys.remove (root^"/liberty.txt");Unix.symlink "netlist.txt" (root^"/liberty.txt"));
  negative "path escape" (fun root -> rewrite_audit root (fun a -> match get a "sources" with
    | `List (x::xs) -> set a "sources" (arr (set x "path" (s "../manifest.txt")::xs)) | _ -> assert false));
  negative "duplicate source" (fun root -> rewrite_audit root (fun a -> match get a "sources" with
    | `List (x::xs) -> set a "sources" (arr (x::x::xs)) | _ -> assert false));
  List.iter (fun key -> negative ("receipt context "^key) (fun root -> rewrite_audit root (fun a ->
    set a key (if key="build_identity" then s (String.make 64 'a') else s "wrong")))) ["build_identity";"audit_id";"stage";"corner"];
  negative "producer mismatch" (fun root -> rewrite_audit root (fun a -> set a "producer" (set producer "version" (s "other"))));
  negative "unknown field" (fun root -> rewrite_audit root (function `Assoc xs -> obj (("pass",`Bool true)::xs) | _ -> assert false));
  negative "duplicate JSON key" (fun root -> write (root^"/audit.json") "{\"schema\":1,\"schema\":1}");
  negative "nonfinite JSON" (fun root -> write (root^"/audit.json") "{\"schema\":NaN}");
  negative "no completion" ~files:(fixture ~completion:false ()) (fun _ -> ());
  negative "nonzero process with printed completion" ~files:(fixture ~exit_code:1 ()) (fun _ -> ());
  negative "native producer version mismatch" ~files:(fixture ~version:"different-version\n" ()) (fun _ -> ());
  negative "coherent manifest/hash with wrong SDC declarations" ~files:(fixture ~sdc:"# no timing intent\n" ()) (fun _ -> ());
  negative "native error" ~files:(fixture ~prefix:"Error: failed command\n" ()) (fun _ -> ());
  negative "missing resolution" ~files:(fixture ~resolution:"\n" ()) (fun _ -> ());
  negative "truncated wire" ~files:(fixture ~resolution:(String.trim (wire events)) ()) (fun _ -> ());
  negative "requested only" ~files:(fixture ~events:[] ()) (fun _ -> ());
  negative "repeated SDC" ~files:(fixture ~events:(events@events) ()) (fun _ -> ());
  negative "reordered events" ~files:(fixture ~events:(List.rev events) ()) (fun _ -> ());
  negative "applied scope false claim" ~files:(fixture ~events:(map_event "applied" (fun e -> set e "targets" (arr [s "u1/D"]))) ()) (fun _ -> ());
  negative "coherent resolved/applied scope false claim" ~files:(fixture ~events:(List.map (fun e ->
    if List.mem (get e "event") [s "resolved";s "applied"] then set e "targets" (arr [s "u1/D"]) else e) events) ()) (fun _ -> ());
  negative "wrong source" ~files:(fixture ~events:(map_event "resolved" (fun e -> set e "source" (s "other"))) ()) (fun _ -> ());
  negative "wrong master" ~files:(fixture ~events:(map_event "stage" (fun e -> set e "master" (s "unsupported"))) ()) (fun _ -> ());
  negative "merged stages" ~files:(fixture ~events:(List.map (fun e -> if get e "event"=s "stage" && get e "id"=s "rx1" then mapped "rx1" "u0" else e) events) ()) (fun _ -> ());
  negative "wrong pin role" ~files:(fixture ~events:(map_event "stage" (fun e -> set e "data_pin" (s "u0/CLK"))) ()) (fun _ -> ());
  negative "wrong counts" ~files:(fixture ~events:(map_event "validated" (fun e -> set e "stage_count" (`Int 3))) ()) (fun _ -> ());
  negative "downstream capture declaration" ~files:(fixture ~intent:(set intent "exceptions" (arr [set ex "targets" (arr [s "rx1"])])) ()) (fun _ -> ());
  negative "blank reason" ~files:(fixture ~intent:(set intent "exceptions" (arr [set ex "reason" (s " ")])) ()) (fun _ -> ());
  Printf.printf "Timing intent evidence: %d positive/negative checks (real bundle envelope; synthetic STA; no EDA).\n" !checks
