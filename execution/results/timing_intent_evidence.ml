module E = Evidence_json
let s x = `String x
let obj x = `Assoc x
let arr x = `List x
let get j k = match j with `Assoc xs -> Option.value (List.assoc_opt k xs) ~default:`Null | _ -> `Null
let required j k = match j with
  | `Assoc xs -> (match List.assoc_opt k xs with Some v -> v
    | None -> invalid_arg ("missing bundle configuration key: "^k))
  | _ -> invalid_arg "expected bundle configuration object"
let text = function
  | `String x when String.trim x<>"" && String.length x<=4096 && not (String.contains x '\000') -> x
  | _ -> invalid_arg "expected bounded nonempty audit string"
let list = function `List xs -> xs | _ -> invalid_arg "expected audit array"
let exact j keys = match j with
  | `Assoc xs when List.sort String.compare keys=List.sort String.compare (List.map fst xs) -> ()
  | _ -> invalid_arg "invalid audit object fields"
let unique label xs =
  if List.length xs<>List.length (List.sort_uniq String.compare xs) then invalid_arg ("duplicate "^label)
let strict raw =
  (match Identity_json.parse raw with Ok _ -> () | Error e -> invalid_arg (Identity_json.error_message e));
  Yojson.Safe.from_string raw
let canonical j = match Identity_json.parse (Yojson.Safe.to_string j) with
  | Ok j -> Identity_json.canonical j | Error e -> invalid_arg (Identity_json.error_message e)
let equal a b = canonical a=canonical b
let digest j =
  let d=text j in
  if String.length d<>64 || not (String.for_all (function '0'..'9'|'a'..'f' -> true | _ -> false) d)
  then invalid_arg "invalid audit SHA-256"; d
let relative p = Filename.is_relative p && p<>"" && not (String.contains p '\000') &&
  List.for_all (fun x -> x<>"" && x<>"." && x<>"..") (String.split_on_char '/' p)
let member root path =
  if not (relative path) then invalid_arg "unsafe audit member path";
  let parts=String.split_on_char '/' path in
  let rec walk dir = function
    | [] -> invalid_arg "empty audit path"
    | x::xs ->
      let p=Filename.concat dir x in
      let st=Unix.lstat p in
      if xs=[] then (if st.Unix.st_kind<>Unix.S_REG then invalid_arg "audit source is not a regular file";p)
      else (if st.Unix.st_kind<>Unix.S_DIR then invalid_arg "audit source ancestor is not a real directory";walk p xs)
  in walk root parts
let read ~total ~limit path =
  let fd=Unix.openfile path [Unix.O_RDONLY;Unix.O_NONBLOCK] 0 in
  let ic=Unix.in_channel_of_descr fd in
  Fun.protect ~finally:(fun () -> close_in_noerr ic) (fun () ->
    let before=Unix.fstat fd in
    if before.st_kind<>Unix.S_REG || before.st_size<=0 || before.st_size>limit then invalid_arg "empty or oversized audit source";
    total:= !total+before.st_size;
    if !total>256*1024*1024 then invalid_arg "audit exceeds 256MiB";
    let raw=really_input_string ic before.st_size in
    let after=Unix.fstat fd in
    if before.st_dev<>after.st_dev || before.st_ino<>after.st_ino || before.st_size<>after.st_size ||
       before.st_mtime<>after.st_mtime || before.st_ctime<>after.st_ctime
    then invalid_arg "audit source changed during read";raw)
let singleton label = function [x] -> x | _ -> invalid_arg ("missing/ambiguous audit "^label)
let input_roles=["manifest";"sdc";"netlist";"liberty";"driver"]
let roles=input_roles@["receipt";"log";"resolution";"exceptions";"unconstrained";"downstream_min";"downstream_max";"process_result";"producer_version"]
let native_roles=["native_producer";"native_sdc";"native_data";"native_checks";"native_endpoints"]

let identifier j =
  let x=text j in
  let letter = function 'a'..'z'|'A'..'Z'|'_' -> true | _ -> false in
  if not (letter x.[0]) || not (String.for_all (fun c -> letter c || (c>='0' && c<='9')) x)
  then invalid_arg "invalid timing intent identifier";x
let integer = function `Int n when n>=0 && n<=256 -> n | _ -> invalid_arg "invalid bounded timing intent count/index"
let physical manifest = get manifest "timing_physical_qualification"<>`Null
let validate_physical manifest =
  if physical manifest then (
    let expected=obj ["schema_version",`Int 1;"resolution_policy",s "scalar-q-driver-cmos5l-physical-v2";
      "analysis_corners",s "all_configured";"analysis_stages",arr [s "synthesized_netlist";s "post_pnr"];
      "sequential_inventory",s "exact_declared_stages";"max_buffer_hops",`Int 8;
      "endpoint_map",s "::hc_pt7::checked_endpoints";"trace_inventory",s "::hc_pt7::checked_traces"] in
    if not (equal (get manifest "timing_physical_qualification") expected) || get manifest "macro_contract"<>`Null then
      invalid_arg "unsupported physical timing qualification contract")
let requested manifest =
  validate_physical manifest;
  let intent=get manifest "timing_intent" in
  exact intent ["contract";"schema_version";"resolution_policy";"stages";"exceptions";"sdc"];
  if get intent "contract"<>s "hardcaml-asic.timing-intent" || get intent "schema_version"<>`Int 1 ||
      get intent "resolution_policy"<>s (if physical manifest then "scalar-q-driver-cmos5l-physical-v2" else "scalar-q-driver-cmos5l-v1") || get intent "sdc"<>s "constraints/top.sdc"
  then invalid_arg "unsupported timing intent contract";
  let stages=list (get intent "stages") and exceptions=list (get intent "exceptions") in
  if stages=[] || List.length stages>256 || List.length exceptions>256 then invalid_arg "missing/bounded timing intent inventory";
  let ids label xs = let ids=List.map (fun j -> identifier (get j "id")) xs in
    unique label ids;
    if ids<>List.sort String.compare ids then invalid_arg ("unsorted "^label);ids in
  let stage_ids=ids "stage ID" stages in
  ignore (ids "exception ID" exceptions);
  List.iter (fun stage ->
    exact stage ["id";"chain";"index";"anchor";"preservation"];
    ignore (identifier (get stage "chain"));ignore (integer (get stage "index"));
    if get stage "anchor"<>s ("hc_pt7_"^text (get stage "id")) then invalid_arg "invalid reserved Q anchor";
    if get stage "preservation"<>s "yosys_keep_1" then invalid_arg "unsupported preservation intent") stages;
  unique "stage anchor" (List.map (fun stage -> text (get stage "anchor")) stages);
  let chains=List.map (fun stage -> text (get stage "chain")) stages |> List.sort_uniq String.compare in
  List.iter (fun chain ->
    let indices=List.filter (fun stage -> get stage "chain"=s chain) stages |>
      List.map (fun stage -> integer (get stage "index")) |> List.sort Int.compare in
    if List.length indices<2 || indices<>List.init (List.length indices) Fun.id then invalid_arg "invalid timing chain indices") chains;
  let scopes=ref [] in
  List.iter (fun ex ->
    exact ex ["id";"kind";"reason";"source";"targets"];
    ignore (text (get ex "reason"));
    let source=get ex "source" in
    exact source ["direction";"port";"bit"];
    if get source "direction"<>s "input" then invalid_arg "exception source is not input";
    if text (get source "port")="clk" then invalid_arg "exception source cannot be primary clock";
    (match get source "bit" with `Null -> () | `Int n when n>=0 && n<1048576 -> () | _ -> invalid_arg "invalid input bit");
    let targets=list (get ex "targets") |> List.map text in
    if targets=[] then invalid_arg "empty timing exception targets";
    unique "exception target" targets;
    if targets<>List.sort String.compare targets || not (List.for_all (fun id -> List.mem id stage_ids) targets)
    then invalid_arg "unknown/unsorted exception targets";
    List.iter (fun target ->
      let scope=text (get ex "kind"),target in
      if List.mem scope !scopes then invalid_arg "overlapping exception target scope";
      scopes:=scope::!scopes) targets;
    match get ex "kind",targets with
    | `String "async_reset",_ -> ()
    | `String "async_capture",[id] ->
      let stage=List.find (fun stage -> get stage "id"=s id) stages in
      if get stage "index"<>`Int 0 then invalid_arg "capture targets downstream stage"
    | _ -> invalid_arg "unsupported timing exception kind/scope") exceptions;
  intent,stages,exceptions

let validate_qualification manifest = ignore (requested manifest)
let wire_prefix="HC_ASIC_TIMING_V1\t"
let iter_lines ~max_lines raw f =
  let length=String.length raw in
  let rec loop start number = if start<length then (
    if number>max_lines then invalid_arg "audit log/stream line budget exceeded";
    let stop=Option.value (String.index_from_opt raw start '\n') ~default:length in
    if stop-start>65536 then invalid_arg "audit log/stream line exceeds 64KiB";
    f number (if stop=start then "" else String.sub raw start (stop-start));
    loop (stop+1) (number+1)) in
  loop 0 1
let diagnostic_line raw =
  (* Only leading ANSI SGR decoration is ignored. Quoted report text and INFO
     messages containing the word ERROR are not fatal-line matches. *)
  let rec strip count line =
    let line=String.trim line in
    if count<16 && String.starts_with ~prefix:"\027[" line then (
      let rec end_sgr i =
        if i>=String.length line || i>34 then None
        else match line.[i] with
          | 'm' -> Some (i+1)
          | '0'..'9'|';' -> end_sgr (i+1)
          | _ -> None in
      match end_sgr 2 with Some i -> strip (count+1) (String.sub line i (String.length line-i)) | None -> line)
    else line in
  strip 0 raw
let fatal line =
  let line=diagnostic_line line in
  List.exists (fun prefix -> String.starts_with ~prefix line)
    ["Error:";"Error ";"PT7_ERROR";"[ERROR ";"[ERROR]";"[FATAL ";"[FATAL]"] || line="Error"
type log_scan = { wire : string list; logged_receipt : E.t }
let scan_log ?(native=false) raw =
  let wire=ref [] and wire_count=ref 0 and wire_bytes=ref 0 in
  let receipt=ref None and completion=ref None and first=ref None and last=ref 0 and native_completion=ref None in
  iter_lines ~max_lines:65536 raw (fun number line ->
    if fatal line then invalid_arg "retained STA error";
    if String.starts_with ~prefix:wire_prefix line then (
      incr wire_count;wire_bytes:= !wire_bytes+String.length line;
      if !wire_count>1028 || !wire_bytes>2*1024*1024 then invalid_arg "audit timing-wire budget exceeded";
      if !first=None then first:=Some number;last:=number;wire:=line::!wire)
    else if String.starts_with ~prefix:"PT7_AUDIT_RECEIPT " line then (
      if !receipt<>None then invalid_arg "duplicate log receipt";
      let prefix=String.length "PT7_AUDIT_RECEIPT " in
      receipt:=Some (number,strict (String.sub line prefix (String.length line-prefix))))
    else if native && line="HC_ASIC_NATIVE_V2_COMPLETE" then (
      if !native_completion<>None then invalid_arg "duplicate native completion";
      native_completion:=Some number)
    else if line="PT7_AUDIT_COMPLETE" then (
      if !completion<>None then invalid_arg "duplicate audit completion";completion:=Some number));
  match !receipt,!completion,!first with
  | Some (r,logged_receipt),Some c,Some first when r<first && c> !last ->
    if native && not (match !native_completion with Some n -> n> !last && n<c | None -> false)
    then invalid_arg "missing/reordered native producer completion";
    {wire=List.rev !wire;logged_receipt}
  | _ -> invalid_arg "missing/reordered producer receipt/completion or timing block"
let replay ~manifest ~resolution ~log_wire =
  let intent,stages,exceptions=requested manifest in
  if not (String.ends_with ~suffix:"\n" resolution) then invalid_arg "truncated resolution stream";
  let retained=ref [] and bytes=ref 0 in
  iter_lines ~max_lines:1028 resolution (fun _ line ->
    bytes:= !bytes+String.length line;
    if !bytes>2*1024*1024 then invalid_arg "resolution stream byte budget exceeded";
    retained:=line::!retained);
  let retained=List.rev !retained in
  if retained<>log_wire then invalid_arg "resolution stream differs from retained producer log";
  let events=List.map (fun line ->
    if not (String.starts_with ~prefix:wire_prefix line) then invalid_arg "unexpected resolution stream bytes";
    strict (String.sub line (String.length wire_prefix) (String.length line-String.length wire_prefix))) retained in
  let cursor=ref events in
  let take event keys = match !cursor with
    | [] -> invalid_arg ("missing timing audit event: "^event)
    | j::rest -> cursor:=rest;
      exact j (["schema_version";"event"]@keys);
      if get j "schema_version"<>`Int 1 || get j "event"<>s event then invalid_arg ("reordered timing audit event: expected "^event);j in
  let count_event name =
    let j=take name ["stage_count";"exception_count"] in
    if get j "stage_count"<>`Int (List.length stages) || get j "exception_count"<>`Int (List.length exceptions)
     then invalid_arg "timing audit inventory counts mismatch" in
   let trace label root sink =
     let j=take "trace" ["label";"root";"sink";"nets";"hops"] in
     if get j "label"<>s label || get j "sink"<>s sink then invalid_arg "physical trace label/sink mismatch";
     let actual_root=text (get j "root") in
     Option.iter (fun root -> if actual_root<>root then invalid_arg "physical trace root mismatch") root;
     let nets=list (get j "nets") |> List.map text and hops=list (get j "hops") in
     if List.length hops>8 || List.length nets<>List.length hops+1 then invalid_arg "physical trace hop/net bounds";
     unique "physical trace net" nets;
     List.iter (fun hop ->
       exact hop ["cell";"master";"input";"output"];
       let cell=text (get hop "cell") in
       if not (List.mem (text (get hop "master"))
         ["sg13cmos5l_buf_1";"sg13cmos5l_buf_2";"sg13cmos5l_buf_4";"sg13cmos5l_buf_8";"sg13cmos5l_buf_16";
          "sg13cmos5l_dlygate4sd1_1";"sg13cmos5l_dlygate4sd2_1";"sg13cmos5l_dlygate4sd3_1"]) ||
         get hop "input"<>s (cell^"/A") || get hop "output"<>s (cell^"/X") then invalid_arg "unsupported physical trace transform") hops;
     unique "physical trace cell" (List.map (fun hop -> text (get hop "cell")) hops);
     actual_root in
   count_event "begin";
   let resolved_stages=List.map (fun stage ->
     let id=text (get stage "id") in
     let root=if physical manifest then Some (trace ("anchor for "^id) None (text (get stage "anchor"))) else None in
     let clock=if physical manifest then (
       match !cursor with
       | j::_ -> let sink=text (get j "sink") in ignore (trace ("clock for "^id) (Some "clk") sink);Some sink
       | [] -> invalid_arg "missing physical clock trace") else None in
     let j=take "stage" ["id";"anchor";"cell";"master";"q_pin";"data_pin";"async_pin"] in
    List.iter (fun k -> if get j k<>get stage k then invalid_arg ("mapped stage differs from intent: "^k)) ["id";"anchor"];
     let cell=text (get j "cell") and master=text (get j "master") in
     Option.iter (fun root -> if root<>cell^"/Q" then invalid_arg "physical anchor/Q mismatch") root;
     Option.iter (fun clock -> if clock<>cell^"/CLK" then invalid_arg "physical clock endpoint mismatch") clock;
    if get j "q_pin"<>s (cell^"/Q") || get j "data_pin"<>s (cell^"/D") then invalid_arg "mapped stage Q/data pin role mismatch";
    let async=match master with
      | "sg13cmos5l_dfrbpq_1" | "sg13cmos5l_dfrbpq_2"
      | "sg13cmos5l_dfrbp_1" | "sg13cmos5l_dfrbp_2" -> s (cell^"/RESET_B")
      | _ -> invalid_arg "unsupported mapped sequential master" in
    if get j "async_pin"<>`Null && get j "async_pin"<>async then invalid_arg "mapped stage async pin role mismatch";j) stages in
   unique "mapped stage cell" (List.map (fun stage -> text (get stage "cell")) resolved_stages);
   if physical manifest then (
     let inventory=take "inventory" ["cells"] in
     let cells=List.map (fun row -> get row "cell") resolved_stages |> List.sort compare in
     if get inventory "cells"<>arr cells then invalid_arg "physical sequential inventory mismatch";
     List.iter2 (fun stage mapped ->
       let index=integer (get stage "index") in
       if index>0 then (
         let previous=List.find (fun other -> get other "chain"=get stage "chain" && get other "index"=`Int (index-1)) stages in
         let previous=List.find (fun other -> get other "id"=get previous "id") resolved_stages in
         ignore (trace ("interstage for "^text (get stage "id")) (Some (text (get previous "q_pin"))) (text (get mapped "data_pin"))))) stages resolved_stages);
   let resolved=List.map (fun ex ->
     if physical manifest then (
       let source=get ex "source" in
       let port=text (get source "port") in
       let port=match get source "bit" with `Null -> port | `Int n -> port^"["^string_of_int n^"]" | _ -> assert false in
       List.iter (fun id ->
         let stage=List.find (fun stage -> get stage "id"=id) resolved_stages in
         let pin=if get ex "kind"=s "async_capture" then "data_pin" else "async_pin" in
         ignore (trace (text (get ex "kind")^" for "^text id) (Some port) (text (get stage pin)))) (list (get ex "targets")));
     let j=take "resolved" ["id";"kind";"source";"targets"] in
    List.iter (fun k -> if get j k<>get ex k then invalid_arg ("resolved exception differs from request: "^k)) ["id";"kind"];
    let source=get ex "source" in
    let port=text (get source "port") in
    let port=match get source "bit" with `Null -> port | `Int n -> port^"["^string_of_int n^"]" | _ -> assert false in
    if get j "source"<>s port then invalid_arg "resolved input differs from request";
    let targets=List.map (fun id ->
      let stage=List.find (fun stage -> get stage "id"=id) resolved_stages in
      let pin=get stage (if get ex "kind"=s "async_capture" then "data_pin" else "async_pin") in
      if pin=`Null then invalid_arg "reset target has no asynchronous control";pin) (list (get ex "targets")) in
    if get j "targets"<>arr targets then invalid_arg "resolved exception pin scope mismatch";j) exceptions in
  count_event "validated";
  let applied=List.map (fun resolved ->
    let j=take "applied" ["id";"kind";"source";"targets"] in
    List.iter (fun k -> if get j k<>get resolved k then invalid_arg ("applied/resolved mismatch: "^k)) ["id";"kind";"source";"targets"];j) resolved in
  count_event "complete";
  if !cursor<>[] then invalid_arg "extra/repeated timing audit block";
   let topology=if physical manifest then Some (obj ["status",s "producer_reported_checked_traces";
     "traces",arr (List.filter (fun event -> get event "event"=s "trace") events);
     "sequential_inventory",get (List.find (fun event -> get event "event"=s "inventory") events) "cells"])
     else None in
   intent,resolved_stages,resolved,applied,topology

(* Replay the library-owned serializer rather than duplicating a Tcl parser.
   async presence is not in the manifest; the reported stage inventory supplies
   it, then the exact emitted SDC suffix must corroborate that assertion. *)
let replay_sdc ~intent ~resolved_stages ~sdc =
  let module T = Hardcaml_asic.Timing in
  let stages=List.map (fun stage ->
    let id=text (get stage "id") in
    let typed=T.Stage.create ~id ~chain:(text (get stage "chain")) ~index:(integer (get stage "index")) in
    let mapped=List.find (fun j -> get j "id"=s id) resolved_stages in
    typed,text (get stage "anchor"),get mapped "async_pin"<>`Null) (list (get intent "stages")) in
  let exceptions=List.map (fun ex ->
    let source=get ex "source" in
    let port=T.Port.input (text (get source "port")) in
    let source=match get source "bit" with `Null -> port | `Int n -> T.Port.bit port n | _ -> assert false in
    let targets=List.map (fun id -> let typed,_,_=List.find (fun (typed,_,_) -> typed.T.Stage.id=text id) stages in typed) (list (get ex "targets")) in
    let id=text (get ex "id") and reason=text (get ex "reason") in
    if get ex "kind"=s "async_capture" then T.Exception.async_capture ~id ~reason ~source ~first_stage:(List.hd targets)
    else T.Exception.async_reset ~id ~reason ~source ~targets) (list (get intent "exceptions")) in
   let expected=Hardcaml_asic.Elaboration.Timing_tcl.render ~stages ~exceptions in
  if not (String.ends_with ~suffix:expected sdc) then invalid_arg "SDC timing intent differs from library serializer replay"

let manifest_envelope manifest =
  Manifest_metadata.validate manifest;
  let fields=["identity";"schema_version";"project";"top_module";"source_revision";"source_inputs";
    "files";"synthesis_sources";"simulation_sources";"resources";"resource_snapshot";
    "simulation_resources";"timing_intent";"target";"adapter";"operation";"resolved_settings";
    "flow_configuration";"generated_settings";"requested_tools"] in
  let has_macro=get manifest "macro_contract"<>`Null in
  let has_native=get manifest "timing_native_coverage"<>`Null in
  exact manifest (fields@(if has_macro then ["macro_contract"] else [])@
    (if has_native then ["timing_native_coverage"] else [])@
    (if physical manifest then ["timing_physical_qualification"] else [])@
    (match manifest with `Assoc xs when List.mem_assoc "hardware_variant" xs -> ["hardware_variant"] | _ -> []));
  validate_physical manifest;
  if has_native then (
    let native=get manifest "timing_native_coverage" in
    exact native ["contract";"schema";"profile";"producer"];
    let version schema profile = obj ["contract",s "hardcaml-asic.timing-native-coverage";"schema",`Int schema;
      "profile",s profile;"producer",s "constraints/pt7-native-coverage.tcl"] in
    if not (List.exists (equal native)
      [version 1 "opensta-2.7-native-state-v1";version 2 "opensta-2.7-native-state-v2"])
    then invalid_arg "unsupported retained native producer contract");
  if get manifest "schema_version"<>`Int 1 || get manifest "adapter"<>s "LibreLane" ||
     not (List.mem (get manifest "operation") [s "Synthesis";s "Hardening"])
  then invalid_arg "unsupported bundle envelope/schema/adapter/operation";
  ignore (text (get manifest "project"));
  let top=identifier (get manifest "top_module") in
  let revision=text (get manifest "source_revision") in
  if not (List.mem (String.length revision) [40;64]) ||
     not (String.for_all (function '0'..'9'|'a'..'f' -> true | _ -> false) revision)
  then invalid_arg "invalid bundle source revision";
  List.iter (fun k -> ignore (list (get manifest k))) ["resources";"simulation_resources"];
  let files=list (get manifest "files") in
  if files=[] || List.length files>512 then invalid_arg "missing/bounded bundle file inventory";
  let paths=List.map (fun file ->
    exact file ["path";"sha256"];
    let path=text (get file "path") in
    if not (relative path) || path="manifest.json" then invalid_arg "unsafe bundle member";
    ignore (digest (get file "sha256"));path) files in
  unique "bundle member" paths;
  if paths<>List.sort String.compare paths then invalid_arg "unsorted bundle file inventory";
  let file path=singleton ("bundle member "^path) (List.filter (fun j -> get j "path"=s path) files) in
  List.iter (fun path -> ignore (file path)) ["constraints/top.sdc";"src/config.json";"info.yaml";"src/"^top^".v"];
  List.iter (fun key ->
    let members=list (get manifest key) |> List.map text in
    if members=[] || List.length members>512 then invalid_arg "missing/bounded bundle source inventory";
    unique key members;
    List.iter (fun path -> ignore (file path)) members) ["synthesis_sources";"simulation_sources"];
  if get manifest "synthesis_sources"<>arr [s ("src/"^top^".v")] then invalid_arg "bundle synthesis/top mismatch";
  if not (List.mem (get manifest "simulation_sources")
    [arr [s ("src/"^top^".v")];arr [s ("simulation/"^top^".v")]])
  then invalid_arg "bundle simulation RTL/top mismatch";
  let inputs=list (get manifest "source_inputs") in
  if inputs=[] || List.length inputs>512 then invalid_arg "missing/bounded bundle source inputs";
  let input_paths=List.map (fun input ->
    exact input ["path";"copy";"sha256";"git_status"];
    let path=text (get input "path") and copy=text (get input "copy") in
    if not (relative path) || copy<>"inputs/"^path then invalid_arg "invalid bundle input copy";
    (match get input "git_status" with `String _ -> () | _ -> invalid_arg "invalid bundle source state");
    if digest (get input "sha256")<>digest (get (file copy) "sha256") then invalid_arg "bundle input/member hash mismatch";path) inputs in
  unique "bundle source input" input_paths;
  let target=get manifest "target" in
  exact target ["harness";"technology";"tiles";"corner";"external_references"];
  List.iter (fun k -> ignore (text (get target k))) ["harness";"technology";"tiles";"corner"];
  let refs=list (get target "external_references") in
  if refs=[] || List.length refs>128 then invalid_arg "missing/bounded bundle target references";
  List.iter (fun r ->
    exact r ["source";"revision";"role";"path";"sha256";"corner"];
    List.iter (fun k -> ignore (text (get r k))) ["source";"revision";"role";"path"];
    if get r "sha256"<>`Null then ignore (digest (get r "sha256"));
    if get r "corner"<>`Null then ignore (text (get r "corner"))) refs;
  let floorplan=singleton "floorplan reference" (List.filter (fun r -> get r "role"=s "Floorplan_def") refs) in
  let floorplan_path=text (get floorplan "path") in
  if get floorplan "source"<>s "Support_tools" || not (relative floorplan_path)
  then invalid_arg "invalid bundle floorplan reference";
  let object_fields label = function `Assoc xs -> xs | _ -> invalid_arg ("invalid bundle "^label) in
  let config=get manifest "flow_configuration" and generated=get manifest "generated_settings" in
  ignore (object_fields "configuration" config);
  let generated_fields=object_fields "generated settings" generated in
  List.iter (fun (k,v) -> if not (equal (required config k) v) then invalid_arg "bundle generated/configuration mismatch") generated_fields;
  if get config "DESIGN_NAME"<>s top || get generated "VERILOG_FILES"<>arr [s ("dir::"^top^".v")]
  then invalid_arg "bundle configuration/top mismatch";
  List.iter (fun k -> if get generated k<>s "dir::../constraints/top.sdc" then invalid_arg "bundle SDC/configuration binding mismatch")
    ["PNR_SDC_FILE";"SIGNOFF_SDC_FILE"];
  let settings=list (get manifest "resolved_settings") in
  if settings=[] || List.length settings>512 then invalid_arg "missing/bounded resolved settings";
  let setting_keys=List.map (fun setting ->
    exact setting ["key";"value";"owner"];
    let key=text (get setting "key") in
    ignore (text (get setting "owner"));
    let configured=required config key in
    if key="FP_DEF_TEMPLATE" then (
      if get setting "value"<>s floorplan_path || configured<>s ("dir::../tt/"^floorplan_path)
      then invalid_arg "bundle FP_DEF_TEMPLATE/floorplan reference mismatch")
    else if not (equal configured (get setting "value")) then invalid_arg "bundle resolved/configuration mismatch";key) settings in
  unique "bundle resolved setting" setting_keys;
  if not (List.mem "FP_DEF_TEMPLATE" setting_keys) then invalid_arg "missing bundle FP_DEF_TEMPLATE resolved setting";
  let config_raw=Yojson.Safe.pretty_to_string config^"\n" in
  if get (file "src/config.json") "sha256"<>s (Identity_json.sha256_bytes config_raw) then invalid_arg "bundle config/member hash mismatch";
  let tools=get manifest "requested_tools" in
  exact tools ["support_tools_revision";"pdk_revision";"librelane";"python";"runtime_policy"];
  List.iter (fun k -> ignore (text (get tools k))) ["support_tools_revision";"pdk_revision";"librelane";"python"];
  let requested=match Identity_json.parse (Yojson.Safe.to_string tools) with
    | Ok j -> j | Error e -> invalid_arg (Identity_json.error_message e) in
  (match Runtime_contract.policy_from_requested requested with
   | Ok _ -> () | Error e -> invalid_arg ("invalid bundle runtime policy: "^e));
  file "constraints/top.sdc"

let inspect_impl ~directory () =
  let root=Unix.realpath directory and total=ref 0 in
  if (Unix.stat root).st_kind<>Unix.S_DIR then invalid_arg "audit target is not a directory";
  let audit_raw=read ~total ~limit:(1024*1024) (member root "audit.json") in
  let audit=strict audit_raw in
  exact audit ["schema";"kind";"scope";"build_identity";"audit_id";"stage";"producer";"corner";"sources"];
   let extracted=get audit "schema"=`Int 3 in
   let native=List.mem (get audit "schema") [`Int 2;`Int 3] in
   if not (List.mem (get audit "schema") [`Int 1;`Int 2;`Int 3]) || get audit "kind"<>s "hardcaml-asic.timing-intent-audit" ||
      get audit "scope"<>s (if extracted then "standalone_extracted_netlist_sta" else "standalone_synthesized_netlist_sta") ||
      get audit "stage"<>s (if extracted then "post_pnr" else "synthesized_netlist")
  then invalid_arg "unsupported timing-intent audit schema/scope/stage";
  ignore (digest (get audit "build_identity"));
  List.iter (fun k -> ignore (text (get audit k))) ["audit_id";"corner"];
  let producer=get audit "producer" in
  exact producer ["tool";"version"];
  if not (List.mem (get producer "tool") (if native then [s "OpenSTA";s "OpenROAD"] else [s "OpenSTA"]))
  then invalid_arg "unsupported audit producer";
  ignore (text (get producer "version"));
  let sources=list (get audit "sources") in
  if List.length sources>64 then invalid_arg "audit exceeds 64 sources";
   let required_roles=roles@(if native then native_roles else [])@(if extracted then ["spef";"parasitic_annotation";"clock_state"] else []) in
   let receipt_roles=input_roles@(if native then ["native_producer"] else [])@(if extracted then ["spef"] else []) in
  let loaded=List.map (fun source ->
    exact source ["role";"path";"sha256"];
    let role=text (get source "role") and path=text (get source "path") in
    if not (List.mem role required_roles) || path="audit.json" then invalid_arg "unsupported audit source role/path";
    let limit=if List.mem role ["manifest";"receipt";"process_result";"producer_version";"native_producer"] then 1024*1024
      else if List.mem role (["log";"resolution";"exceptions";"unconstrained";"downstream_min";"downstream_max";"driver";"sdc"]@native_roles) then 8*1024*1024
      else 64*1024*1024 in
    let raw=read ~total ~limit (member root path) in
    if Identity_json.sha256_bytes raw<>digest (get source "sha256") then invalid_arg ("audit source hash mismatch: "^path);
    role,(source,raw)) sources in
  unique "audit source path" (List.map (fun source -> text (get source "path")) sources);
  List.iter (fun role -> let xs=List.filter (fun (r,_) -> role=r) loaded in
    if role="liberty" then (if xs=[] then invalid_arg "missing audit liberty")
    else ignore (singleton role xs)) required_roles;
  let source role=snd (singleton role (List.filter (fun (r,_) -> role=r) loaded)) in
  let raw role=snd (source role) in
  if native then (
    let declaration=get (strict (raw "manifest")) "timing_native_coverage" in
    if declaration=`Null then invalid_arg "schema2 requires identity-covered native producer contract";
    if get declaration "schema"<>`Int 2 || get declaration "profile"<>s "opensta-2.7-native-state-v2" then
      invalid_arg "unsupported legacy native producer profile; recapture with category-bound native version 2");
  let receipt=strict (raw "receipt") in
  exact receipt ["schema";"build_identity";"audit_id";"stage";"producer";"corner";"inputs"];
  if get receipt "schema"<>`Int 1 then invalid_arg "unsupported receipt schema";
  List.iter (fun k -> if not (equal (get receipt k) (get audit k)) then invalid_arg ("audit/receipt mismatch: "^k))
    ["build_identity";"audit_id";"stage";"producer";"corner"];
  let inputs=List.filter (fun source -> List.mem (text (get source "role")) receipt_roles) sources in
  if not (equal (get receipt "inputs") (arr inputs)) then invalid_arg "receipt input closure mismatch";
  if String.trim (raw "producer_version")<>text (get producer "version") then invalid_arg "native producer version mismatch";
  let process=strict (raw "process_result") in
  exact process ["schema";"receipt_sha256";"exit_code";"argv"];
  if get process "schema"<>`Int 1 || get process "exit_code"<>`Int 0 then invalid_arg "STA process did not exit successfully";
  if digest (get process "receipt_sha256")<>Identity_json.sha256_bytes (raw "receipt") then invalid_arg "process/receipt binding mismatch";
  let argv=list (get process "argv") in
  if argv=[] || List.length argv>256 then invalid_arg "missing/bounded STA argv";
  List.iter (fun arg -> ignore (text arg)) argv;
  let log=scan_log ~native (raw "log") in
  if not (equal log.logged_receipt receipt) then invalid_arg "log/receipt mismatch";
   let manifest=strict (raw "manifest") in
   if extracted && not (physical manifest) then invalid_arg "physical audit requires explicit physical timing contract";
  let sdc_file=manifest_envelope manifest in
  (match Hardcaml_asic.Elaboration.Bundle.verify_manifest_identity manifest with
   | Ok () -> () | Error _ -> invalid_arg "invalid bundle manifest identity");
  if get manifest "identity"<>get audit "build_identity" then invalid_arg "audit/manifest build mismatch";
  (* Check the complete retained SDC bytes, independently of the supplementary
     Timing intent suffix replay. Altering any prefix cannot retain this bundle identity. *)
  if digest (get sdc_file "sha256")<>Identity_json.sha256_bytes (raw "sdc") then invalid_arg "audit SDC differs from emitted bundle";
   let intent,resolved_stages,resolved,applied,topology=replay ~manifest ~resolution:(raw "resolution") ~log_wire:log.wire in
  let native_coverage=if native then (
    let contract=get manifest "timing_native_coverage" in
    if contract=`Null then invalid_arg "schema2 requires identity-covered native producer contract";
    let producer_path=text (get contract "producer") in
    let file=singleton "native producer bundle member" (List.filter (fun file -> get file "path"=s producer_path) (list (get manifest "files"))) in
    if digest (get file "sha256")<>Identity_json.sha256_bytes (raw "native_producer")
    then invalid_arg "retained native producer differs from emitted bundle";
     match Timing_native_coverage.inspect_with_clock_policy ~allow_propagated:extracted ~top:(text (get manifest "top_module")) ~resolved ~native_sdc:(raw "native_sdc")
      ~native_data:(raw "native_data") ~native_checks:(raw "native_checks")
      ~native_endpoints:(raw "native_endpoints") with
    | Ok j ->
      if extracted then (
        let clocks=strict (raw "clock_state") in
        exact clocks ["schema";"all_propagated";"clocks"];
        if get clocks "schema"<>`Int 1 || get clocks "all_propagated"<>`Int 1 ||
           get clocks "clocks"<>get (get j "stored_clock_propagation") "clocks" then
          invalid_arg "native clock observation contradicts serialized propagated clock state");
      Some j
    | Error e -> invalid_arg e)
  else (replay_sdc ~intent ~resolved_stages ~sdc:(raw "sdc");None) in
   audit,sources,audit_raw,intent,resolved_stages,resolved,applied,process,native_coverage,topology

let inspect ~directory () =
  try
    let audit,sources,audit_raw,intent,resolved_stages,resolved,applied,process,native_coverage,topology=inspect_impl ~directory () in
    Ok (obj ["schema",get audit "schema";"kind",s "hardcaml-asic.timing-intent-evidence";
      "scope",get audit "scope";"verdict",s "unavailable";
      "provenance",audit;"audit_sha256",s (Identity_json.sha256_bytes audit_raw);
      "sources",arr sources;
      "process",process;
      "requested",intent;
      "resolved",obj (["status",s "producer_reported";"stages",arr resolved_stages;"exceptions",arr resolved]@
        (match topology with None -> [] | Some j -> ["physical_topology",j]));
      "applied",obj ["status",s "command_completed";"exceptions",arr applied;
        "meaning",s "set_false_path returned; not native path-effect proof"];
      "source_authentication",s "hash_checked_retained_producer_assertions_not_authenticated_execution";
      "native_coverage",Option.value native_coverage ~default:(obj ["status",s "unavailable";"reason",s "native exception path-effect introspection is not supported";
        "report",List.find (fun source -> get source "role"=s "exceptions") sources]);
      "unconstrained",(match native_coverage with
        | Some j -> obj ["status",s "complete_returned_native_groups";"coverage",s "unavailable";
          "diagnostics",get j "diagnostics";"endpoints",get j "endpoints"]
        | None -> obj ["status",s "retained_not_interpreted";"coverage",s "unavailable";
          "report",List.find (fun source -> get source "role"=s "unconstrained") sources]);
      "downstream",obj ["status",s "retained_not_interpreted";"coverage",s "unavailable";
        "reports",arr (List.filter (fun source -> List.mem (get source "role") [s "downstream_min";s "downstream_max"]) sources)]])
  with
  | Invalid_argument e | Failure e | Sys_error e -> Error e
  | End_of_file -> Error "truncated audit source"
  | Yojson.Json_error e -> Error ("invalid audit JSON: "^e)
  | Unix.Unix_error (e,f,p) -> Error (f^" "^p^": "^Unix.error_message e)
