module J = Identity_json
module E = Evidence_json
let fail s = invalid_arg ("workload: " ^ s)
let checked = function Ok x -> x | Error e -> fail (J.error_message e)
let obj xs = checked (J.object_ xs)
let str s = checked (J.string s)
let num n = checked (J.integer (string_of_int n))
let arr xs = J.array xs
let get j k = match j with J.Object xs -> (match List.assoc_opt k xs with Some v -> v | None -> fail ("missing "^k)) | _ -> fail "expected object"
let exact j keys = ignore (checked (J.exact_object j ~fields:keys))
let text = function J.String s -> s | _ -> fail "expected string"
let integer = function J.Integer s -> (try int_of_string s with _ -> fail "integer out of range") | _ -> fail "expected integer"
let bounded lo hi j = let n=integer j in if n<lo || n>hi then fail "integer bounds"; n
let boolean = function J.Bool b -> b | _ -> fail "expected boolean"
let list = function J.Array xs -> xs | _ -> fail "expected array"
let equal a b = J.canonical a=J.canonical b
let hash raw = J.sha256_bytes raw
let hex s = String.length s=64 && String.for_all (function '0'..'9'|'a'..'f' -> true | _ -> false) s
let digest j = let s=text j in if not (hex s) then fail "invalid sha256";s
let label j = let s=text j in if s="" || String.length s>128 || not (String.for_all (function 'a'..'z'|'A'..'Z'|'0'..'9'|'.'|'_'|'-' -> true | _ -> false) s) then fail "invalid identifier";s
let parse raw = checked (J.parse raw)
let wire j = Yojson.Safe.from_string (J.canonical j)
let of_e j = parse (E.record_bytes j)
let mib = 1024*1024
let reference r =
  exact r ["path";"sha256";"bytes"];
  let p=text (get r "path") in
  if String.length p>512 || not (Filename.is_relative p) || String.contains p '\\' || String.contains p '\000' ||
     List.exists (fun x -> List.mem x ["";".";".."]) (String.split_on_char '/' p)
  then fail "unsafe member path";
  ignore (digest (get r "sha256"));ignore (bounded 0 (8*mib) (get r "bytes"));p
type operation = { enable:bool; write:bool; address:int; data:int }
let operation j =
  exact j ["enable";"write";"address";"data"];
  {enable=boolean (get j "enable");write=boolean (get j "write");
   address=bounded 0 255 (get j "address");data=bounded 0 65535 (get j "data")}
let sample = function J.Null -> None | j -> Some (bounded 0 65535 j)
let guard f = try Ok (f ()) with
  | Invalid_argument s | Failure s | Sys_error s -> Error s
  | End_of_file -> Error "workload: truncated source"
  | Unix.Unix_error (e,f,p) -> Error (f^" "^p^": "^Unix.error_message e)

let capture path =
  (* Only the explicitly selected parent/root may be canonicalized. Member paths
     are never realpathed before component-wise no-follow descriptor admission. *)
  let root=Unix.realpath (Filename.dirname path) in
  let path=Filename.concat root (Filename.basename path) in
  let sources=ref [] and total=ref 0 in
  let admit ~bound path =
    let raw=Retained_file.read ~max_bytes:bound path in
    total:= !total+String.length raw;
    if !total>32*mib then fail "aggregate source budget exceeded";
    sources:=obj ["path",str path;"sha256",str (hash raw);"bytes",num (String.length raw)]:: !sources;
    raw in
  let read_ref ~bound r =
    let p=reference r in let raw=admit ~bound (Filename.concat root p) in
    if hash raw<>text (get r "sha256") || String.length raw<>integer (get r "bytes") then fail "source reference mismatch";raw in
  let result=parse (admit ~bound:mib path) in
  exact result ["contract";"schema_version";"bundle";"workload";"simulation";"stimulus";"trace";"counters"];
  let version j contract = if get j "contract"<>str contract || get j "schema_version"<>num 1 then fail "unsupported contract/version" in
  version result "hardcaml-asic.workload-result";
  let workload=get result "workload" in
  exact workload ["identity";"protocol";"seed";"depth";"width";"window";"operations";"expected"];
  let workload_id=digest (get workload "identity") in
  if J.identity (checked (J.unsigned workload ~field:"identity"))<>workload_id then fail "workload identity mismatch";
  if get workload "protocol"<>str "memory-1rw-latency1-v1" || get workload "depth"<>num 256 || get workload "width"<>num 16 then fail "unsupported memory protocol";
  ignore (bounded 0 4294967295 (get workload "seed"));
  let ops=list (get workload "operations") in
  let n=List.length ops in if n<1 || n>4096 then fail "operation count bounds";
  let encoded_ops=Array.of_list ops in
  let operations=Array.of_list (List.map operation ops) in
  let expected=Array.of_list (list (get workload "expected")) in
  if Array.length expected<>n then fail "expected observation count mismatch";
  let window=get workload "window" in exact window ["start";"stop"];
  let start=bounded 0 (n-1) (get window "start") and stop=bounded 1 n (get window "stop") in
  if start>=stop then fail "empty/reversed sample window";
  let stimulus_ref=get result "stimulus" and trace_ref=get result "trace" in
  let stimulus=parse (read_ref ~bound:mib stimulus_ref) in exact stimulus ["operations"];
  if not (equal (get stimulus "operations") (get workload "operations")) then fail "stimulus differs from logical operations";
  let trace=parse (read_ref ~bound:mib trace_ref) in exact trace ["rows"];
  let rows=Array.of_list (list (get trace "rows")) in
  if Array.length rows>n then fail "trace suffix exceeds workload";
  let sim_ref=get result "simulation" in
  let sim=parse (read_ref ~bound:mib sim_ref) in
  exact sim ["contract";"schema_version";"attempt_id";"status";"bundle_identity";"hardware_variant_identity";"workload_identity";"stimulus";"trace";"rtl_mode";"artifacts";"adapter";"clock";"model";"measurement"];
  version sim "hardcaml-asic.workload-simulation";
  let attempt=label (get sim "attempt_id") and status=text (get sim "status") in
  if not (List.mem status ["completed";"failed";"interrupted";"partial"]) then fail "invalid simulation status";
  if status="completed" && Array.length rows<>n then fail "completed simulation has incomplete trace";
  List.iter (fun key -> if not (equal (get sim key) (get result key)) then fail ("simulation/result "^key^" mismatch")) ["stimulus";"trace"];
  if get sim "workload_identity"<>str workload_id then fail "simulation workload mismatch";
  List.iter (fun (k,v) -> if get sim k<>str v then fail ("unsupported "^k))
    ["adapter","memory-1rw-latency1-v1";"clock","posedge-single-clock-v1";"measurement","memory-1rw-window-v1"];
  ignore (label (get sim "model"));
  let mode=text (get sim "rtl_mode") in
  if not (List.mem mode ["implementation";"behavioral"]) then fail "unsupported rtl_mode";
  let bundle_path=text (get result "bundle") in
  if Filename.is_relative bundle_path then fail "bundle root must be absolute";
  let bundle_path=Unix.realpath bundle_path in
  let bundle () = match Resource_inspection_service.comparison_snapshot (Bundle bundle_path) () with Ok x -> of_e x | Error e -> fail e in
  let bundle_before=bundle () in
  let manifest=get bundle_before "manifest" in
  let bundle_id=digest (get manifest "identity") in
  let variant=match Hardcaml_asic.Hardware_variant.of_string (J.canonical (get manifest "hardware_variant")) with
    | Ok v -> Hardcaml_asic.Hardware_variant.identity v
    | Error e -> fail (Core.Error.to_string_hum e) in
  if get sim "bundle_identity"<>str bundle_id || get sim "hardware_variant_identity"<>str variant then fail "simulation bundle/hardware association mismatch";
  let artifacts=list (get sim "artifacts") in
  if List.length artifacts<4 || List.length artifacts>32 then fail "artifact count bounds";
  let artifact_refs=List.map (fun a -> exact a ["role";"source"];
    let role=text (get a "role") in if not (List.mem role ["rtl";"model";"testbench";"tool"]) then fail "unsupported artifact role";
    ignore (read_ref ~bound:(8*mib) (get a "source"));role,get a "source") artifacts in
  List.iter (fun role -> if not (List.mem_assoc role artifact_refs) then fail ("missing artifact role "^role)) ["rtl";"model";"testbench";"tool"];
  let unique xs = List.length xs=List.length (List.sort_uniq String.compare xs) in
  if not (unique (List.map (fun (role,r) -> role^":"^reference r) artifact_refs)) then fail "duplicate artifact";
  let selected=list (get manifest (if mode="implementation" then "synthesis_sources" else "simulation_sources")) in
  let files=list (get manifest "files") in
  let selected_hashes=List.map (fun name ->
    let f=match List.find_opt (fun f -> get f "path"=name) files with Some f -> f | None -> fail "RTL outside bundle inventory" in
    text (get f "sha256")) selected |> List.sort String.compare in
  let rtl_hashes=List.filter_map (fun (role,r) -> if role="rtl" then Some (text (get r "sha256")) else None) artifact_refs |> List.sort String.compare in
  if selected_hashes=[] || selected_hashes<>rtl_hashes then fail "actual RTL artifacts differ from selected bundle source set";
  let memory=Array.make 256 None and output=ref None in
  let cycles=ref 0 and reads=ref 0 and writes=ref 0 and defined=ref 0 and latency=ref false and mismatches=ref 0 in
  Array.iteri (fun i op ->
    let before= !output in
    let after=if not op.enable then before else if op.write then (memory.(op.address)<-Some op.data;None) else memory.(op.address) in
    let e=expected.(i) in exact e ["before";"after"];
    if sample (get e "before")<>before || sample (get e "after")<>after then fail "expected observations disagree with independent memory oracle";
    output:=after;
    if i<Array.length rows then (
      let row=rows.(i) in exact row ["cycle";"input";"before";"after"];
      if get row "cycle"<>num i || not (equal (get row "input") encoded_ops.(i)) then fail "trace cycle/input differs from stimulus";
      let observed_before=sample (get row "before") and observed_after=sample (get row "after") in
      List.iter (fun (want,got) -> match want with Some _ when want<>got -> incr mismatches | _ -> ()) [before,observed_before;after,observed_after];
      if i>=start && i<stop then (
        incr cycles;
        if op.enable then (if op.write then incr writes else (incr reads;if after<>None then latency:=true));
        if before<>None then incr defined;if after<>None then incr defined))) operations;
  let counters=obj ["cycles",num !cycles;"reads",num !reads;"writes",num !writes;
    "defined_observations",num !defined;"read_latency",(if !latency then num 1 else J.null);"operations",num (!reads+ !writes)] in
  exact (get result "counters") ["cycles";"reads";"writes";"defined_observations";"read_latency";"operations"];
  if not (equal counters (get result "counters")) then fail "stored counters differ from trace replay";
  let complete=status="completed" && Array.length rows=n in
  let passed=complete && !mismatches=0 in
  let controls=obj ["workload",str workload_id;"adapter",get sim "adapter";"clock",get sim "clock";
    "model",get sim "model";"measurement",get sim "measurement";"rtl_mode",str mode;
    "tool",arr (List.filter_map (fun (role,r) -> if role="tool" then Some (get r "sha256") else None) artifact_refs)] in
  let source_refs=arr (List.rev !sources) in
  let scope=obj ["protocol",str "memory-1rw-latency1-v1";"window",window;"coverage",str "before_and_after_edge_no_initial_or_write_output_claim"] in
  let metric name value quantity unit_ extra = obj (["metric_id",str ("workload/"^name);"name",str name;
    "value",(if passed then value else J.null);"quantity",str quantity;"classification",str "derived";
    "unit",str unit_;"stage",str "functional_simulation";"definition",str ("memory-1rw-window-v1."^name);
    "scope",scope;"controls",controls;"sources",source_refs]@extra) in
  let metrics=List.map (fun (name,unit_) -> metric name (get counters name) "integer" unit_ [])
    ["cycles","cycle";"reads","operation";"writes","operation";"operations","operation";"defined_observations","observation";"read_latency","cycle"] @
    [metric "throughput" (if !cycles=0 then J.null else checked (J.float (float_of_int (!reads+ !writes)/.float_of_int !cycles))) "real" "operation/cycle"
      ["numerator",(if passed then num (!reads+ !writes) else J.null);"denominator",(if passed then num !cycles else J.null)]] in
  if not (equal bundle_before (bundle ())) then fail "bundle generation changed during workload capture";
  let projection=obj ["contract",str "hardcaml-asic.workload-inspection";"schema_version",num 1;
    "binding",obj ["bundle",str bundle_path;"bundle_identity",str bundle_id;"hardware_variant_identity",str variant;"workload_identity",str workload_id;
      "simulation_attempt_id",str attempt;"simulation_sha256",get sim_ref "sha256";"rtl_mode",str mode];
    "controls",controls;"provenance",obj ["sources",source_refs;"bundle_sources",get bundle_before "bundle_sources";
      "artifacts",get sim "artifacts";"stimulus",stimulus_ref;"trace",trace_ref;
      "authentication",str "retained_consistency_not_authenticated_execution"];
    "outcome",obj ["status",str status;"functional",str (if !mismatches>0 then "fail" else if complete then "pass" else "unavailable");
      "performance",str (if passed then "available" else "unavailable");"mismatched_observations",num !mismatches;"recomputed_counters",counters];
    "metrics",arr metrics] in
  let projection=match projection with J.Object xs -> obj (("source_digest",str (J.identity projection))::xs) | _ -> assert false in
  if String.length (J.canonical projection)>mib then fail "projection exceeds 1MiB";
  projection

let inspect path = guard (fun () ->
  let first=capture path in
  let second=capture path in
  if get first "source_digest"<>get second "source_digest" then fail "source generation changed; retry explicit result";
  wire first)
let snapshot = inspect
let recheck path ~expected_digest = match inspect path with
  | Error e -> Error e
  | Ok j -> guard (fun () -> if get (of_e j) "source_digest"<>str expected_digest then fail "stale workload source generation")
