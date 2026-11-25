module E = Evidence_json
let get j k = match j with `Assoc xs -> Option.value (List.assoc_opt k xs) ~default:`Null | _ -> `Null
let text = function `String s when s<>"" -> s | _ -> invalid_arg "Linked timing flow: nonempty string required"
let list = function `List xs -> xs | _ -> invalid_arg "Linked timing flow: array required"
let require b s = if not b then invalid_arg ("Linked timing flow: "^s)
let exact j keys = match j with
  | `Assoc xs when List.sort String.compare (List.map fst xs)=List.sort String.compare keys -> ()
  | _ -> invalid_arg "Linked timing flow: unexpected receipt fields"
let nonnegative = function `Int n when n>=0 -> n | _ -> invalid_arg "Linked timing flow: nonnegative integer required"
let historical_path j =
  let p=text j in
  require (not (Filename.is_relative p)) "absolute historical path required";
  (match Checkpoint.absolute_path p with Ok normalized -> require (normalized=p) "noncanonical historical path"
   | Error e -> invalid_arg e);p
(* Decode a bounded Tcl LIST, not a script. No command/variable substitution,
   interpreter, filesystem lookup or current-root normalization is involved. *)
let tcl_paths raw =
  require (String.length raw<=1024*1024 && not (String.contains raw '\000')) "native Liberty list byte/NUL bound";
  let pos=ref 0 and length=String.length raw in
  let space = function ' '| '\t' | '\n' | '\r' | '\012' | '\011' -> true | _ -> false in
  let peek ()=if !pos<length then Some raw.[!pos] else None in
  let skip ()=while !pos<length && space raw.[!pos] do incr pos done in
  let add b c=Buffer.add_char b c;require (Buffer.length b<=65536) "native Liberty list element bound" in
  let escape b =
    incr pos;require (!pos<length) "truncated native Liberty list escape";
    let c=raw.[!pos] in incr pos;
    match c with
    | '\n' -> add b ' ';while !pos<length && (raw.[!pos]=' ' || raw.[!pos]='\t') do incr pos done
    | 'n' -> add b '\n' | 'r' -> add b '\r' | 't' -> add b '\t'
    | 'v' -> add b '\011' | 'f' -> add b '\012'
    | '\\'|'"'|'{'|'}'|'['|']'|'$'|';'|' ' -> add b c
    | _ -> invalid_arg "Linked timing flow: unsupported native Liberty list escape" in
  let word () =
    let b=Buffer.create 128 in
    (match peek () with
     | Some '{' -> incr pos;
       let depth=ref 1 in
       while !depth>0 do
         require (!pos<length) "unterminated native Liberty list braces";
         let c=raw.[!pos] in incr pos;
         if c='\\' then (
           require (!pos<length) "truncated braced Liberty list escape";
           let next=raw.[!pos] in incr pos;
           if next='\n' then (add b ' ';while !pos<length && (raw.[!pos]=' ' || raw.[!pos]='\t') do incr pos done)
           else (add b c;add b next))
         else if c='{' then (incr depth;require (!depth<=16) "native Liberty list brace depth";add b c)
         else if c='}' then (decr depth;if !depth>0 then add b c)
         else add b c
       done
     | Some '"' -> incr pos;
       while peek ()<>Some '"' do
         require (!pos<length) "unterminated native Liberty list quote";
         if raw.[!pos]='\\' then escape b else (add b raw.[!pos];incr pos)
       done;incr pos
     | Some _ ->
       while !pos<length && not (space raw.[!pos]) do
         if raw.[!pos]='\\' then escape b else (add b raw.[!pos];incr pos)
       done
     | None -> invalid_arg "Linked timing flow: missing Liberty list word");
    require (!pos=length || space raw.[!pos]) "compound native Liberty list word";
    Buffer.contents b in
  let rec loop count acc = skip ();if !pos=length then List.rev acc else (
    require (count<64) "native Liberty list count bound";
    let value=word () in loop (count+1) (value::acc)) in
  loop 0 []
let rec normalize = function
  | `Intlit s -> (match int_of_string_opt s with Some n -> `Int n | None -> `Intlit s)
  | `Assoc xs -> `Assoc (List.map (fun (k,v) -> k,normalize v) xs)
  | `List xs -> `List (List.map normalize xs)
  | v -> v
let rec ordered = function
  | `Assoc xs -> `Assoc (List.sort (fun (a,_) (b,_) -> String.compare a b) (List.map (fun (k,v) -> k,ordered v) xs))
  | `List xs -> `List (List.map ordered xs)
  | v -> v
let equal a b = ordered (normalize a)=ordered (normalize b)
let read p =
  let fd=Unix.openfile p [Unix.O_RDONLY;Unix.O_NONBLOCK] 0 in
  let ic=Unix.in_channel_of_descr fd in
  Fun.protect ~finally:(fun () -> close_in_noerr ic) (fun () ->
    let before=Unix.fstat fd in
    require (before.Unix.st_kind=Unix.S_REG) "metadata is not regular";
    let n=before.st_size in
    require (n<=8*1024*1024) "metadata exceeds 8MiB";
    let raw=really_input_string ic n in
    let after=Unix.fstat fd in
    require (before.st_dev=after.st_dev && before.st_ino=after.st_ino &&
      before.st_size=after.st_size && before.st_mtime=after.st_mtime && before.st_ctime=after.st_ctime)
      "metadata changed while reading";
    match E.parse ~reject_duplicates:true raw with Ok j -> normalize (E.value j) | Error e -> invalid_arg e)
let hash p = match Checkpoint.file_record p with Ok r -> r.sha256 | Error e -> invalid_arg e
let producer = "constraints/pt7-native-coverage.tcl"
let expected m = get m "timing_native_coverage"<>`Null || List.exists (fun r -> get r "path"=`String producer)
  (match get m "files" with `List xs -> xs | _ -> [])
let safe p = p<>"" && Filename.is_relative p && not (String.contains p '\000') &&
  List.for_all (fun s -> s<>"" && s<>"." && s<>"..") (String.split_on_char '/' p)
let member root p =
  require (safe p) "unsafe retained source path";
  let rec walk root = function
    | [] -> root
    | name::rest -> let p=Filename.concat root name in
      let kind=(Unix.lstat p).Unix.st_kind in
      require (kind=(if rest=[] then Unix.S_REG else Unix.S_DIR)) "source is missing, linked or not regular";
      walk p rest in
  walk root (String.split_on_char '/' p)
let ref_with_hash hash root relative = let p=member root relative in
  `Assoc ["path",`String relative;"sha256",`String (hash p)]
let check_with_hash hash root r =
  let path=text (get r "path") in
  require (`String (hash (member root path))=get r "sha256") ("changed source: "^path); path
let ref_ = ref_with_hash hash
let check_ref = check_with_hash hash
let protect f = try Ok (f ()) with
  | Invalid_argument s | Failure s | Sys_error s -> Error s
  | Unix.Unix_error (e,f,p) -> Error (f^" "^p^": "^Unix.error_message e)
  | End_of_file -> Error "Linked timing flow: truncated source"
  | Yojson.Json_error s -> Error s
let manifest ~root context =
  let p=member root "project/manifest.json" in
  require (get context "manifest_sha256"=`String (hash p)) "run/manifest hash mismatch";
  let m=read p in
  require (get context "build_identity"=get m "identity") "run/build identity mismatch";m
let inventory root =
  let count=ref 0 and total=ref 0 in
  let rec walk relative =
    let p=Filename.concat root relative in
    match (Unix.lstat p).Unix.st_kind with
    | Unix.S_DIR when relative="timing-intent/toolbox" -> []
    | Unix.S_DIR -> Array.to_list (Sys.readdir p) |> List.sort String.compare |>
      List.concat_map (fun n -> walk (relative^"/"^n))
    | Unix.S_REG ->
      incr count; require (!count<=512) "source count bound exceeded";
      let bytes=(Unix.stat p).Unix.st_size in
      require (bytes<=64*1024*1024) "source exceeds 64MiB";
      total:= !total+bytes; require (!total<=512*1024*1024) "source bytes exceed 512MiB";
      if relative="timing-intent/analysis.json" then [] else [ref_ root relative]
    | _ -> invalid_arg "Linked timing flow: linked/special analysis member" in
  walk "timing-intent"
let publish ~run_dir ~context ~succeeded () =
  let root=Unix.realpath run_dir in
  let m=manifest ~root context in
  require (expected m) "producer not advertised";
  let extra=["project/manifest.json";"project/constraints/top.sdc";"project/"^producer] in
  let extra=if Sys.file_exists (root^"/timing-intent/runtime-result.json") then (
    let h=read (root^"/timing-intent/runtime-result.json") in
    extra @ List.map (fun k -> check_ref root (get (get h "synthesis") k)) ["config";"state";"netlist";"resolved"])
    else extra in
  let sources=inventory root @ List.map (ref_ root) extra in
  let payload=`Assoc ["schema",`Int 1;"kind",`String "hardcaml-asic.timing-intent-flow";
    "run_id",get context "run_id";"build_identity",get context "build_identity";
    "manifest_sha256",get context "manifest_sha256";"stage",`String "synthesized_netlist";
    "status",`String (if succeeded then "completed" else "failed");
    "sources",`List sources] in
  let path=root^"/timing-intent/analysis.json" in
  require (not (Sys.file_exists path)) "companion already exists";
  let bytes=E.record_bytes payload in
  let fd=Unix.openfile path [Unix.O_WRONLY;Unix.O_CREAT;Unix.O_EXCL] 0o600 in
  let oc=Unix.out_channel_of_descr fd in
  Fun.protect ~finally:(fun () -> close_out_noerr oc) (fun () -> output_string oc bytes)
type named = {
  directory : string;
  publication : E.t;
  synthesis : E.t;
  binding : E.t;
  companion : E.t;
}
let inspect_impl ?named ~run_dir ~context () =
  let source_limit=match named with None -> 512 | Some _ -> Timing_intent_inventory.max_sources in
  let byte_limit=match named with None -> 64*1024*1024 | Some _ -> Timing_intent_inventory.max_source_bytes in
  let hash=match named with None -> hash | Some _ -> (fun p -> Timing_intent_inventory.hash p) in
  let ref_=ref_with_hash hash and check_ref=check_with_hash hash in
  let context=normalize context in
  let root=Unix.realpath run_dir in
  let m=manifest ~root context in
  Manifest_metadata.validate m;
  if not (expected m) then (
    require (get context "timing_intent_expected"<>`Bool true) "new run's advertised producer declaration is missing";
    `Assoc ["status",`String "unavailable";
    "reason",`String "historical bundle has no native timing producer";"verdict",`String "unavailable";
    "sources",`List []])
  else (
    (match Hardcaml_asic.Elaboration.Bundle.verify_manifest_identity m with
     | Ok () -> () | Error _ -> invalid_arg "Linked timing flow: invalid bundle identity");
    let native_contract=get m "timing_native_coverage" in
    exact native_contract ["contract";"schema";"profile";"producer"];
    require (get native_contract "contract"=`String "hardcaml-asic.timing-native-coverage" &&
      get native_contract "schema"=`Int 2 && get native_contract "producer"=`String producer &&
      get native_contract "profile"=`String "opensta-2.7-native-state-v2")
      "unsupported legacy/native producer declaration; recapture with native profile 2";
    let analysis p = (match named with None -> "timing-intent" | Some n -> n.directory)^"/"^p in
    (match named with None -> require (get context "requested_stage"=`String "synthesis") "only coarse synthesis is supported" | Some _ -> ());
    let a_path=member root (analysis "analysis.json") in
    let a=match named with None -> read a_path | Some n -> n.companion in
    require (get a "schema"=`Int 1 && get a "kind"=`String (match named with None -> "hardcaml-asic.timing-intent-flow" | Some _ -> "hardcaml-asic.timing-intent-execution")) "unsupported companion";
    List.iter (fun k -> require (get a k=get context k) ("companion/run mismatch: "^k))
      ((match named with None -> ["run_id"] | Some n ->
        require (equal (get a "publication") n.publication) "companion/publication mismatch";[]) @ ["build_identity";"manifest_sha256"]);
    require (get a "stage"=`String "synthesized_netlist") "wrong analysis stage";
    let sources=list (get a "sources") in
    require (List.length sources<=source_limit) "too many retained sources";
    let total=ref 0 in
    List.iter (fun source ->
      let p=member root (text (get source "path")) in
      let bytes=(Unix.stat p).Unix.st_size in
      require (bytes<=byte_limit) (if named=None then "retained source exceeds 64MiB" else "retained named source exceeds 128MiB");
      total:= !total+bytes; require (!total<=512*1024*1024) "retained sources exceed 512MiB") sources;
    let paths=List.map (check_ref root) sources in
    require (List.length paths=List.length (List.sort_uniq String.compare paths)) "duplicate source path";
    let bound r = let path=check_ref root r in
      require (List.exists (fun s -> get s "path"=get r "path" && get s "sha256"=get r "sha256") sources)
        ("source outside companion: "^path);path in
    let retained p = require (List.mem p paths) ("missing bound source: "^p); read (member root p) in
    let request=retained (analysis "request.json") in
    let historical_root=historical_path (get request "execution_root") in
    let executed relative=historical_root^"/"^relative in
    let pdk_root=historical_path (get request "pdk_root") in
    let uid=nonnegative (get request "host_uid") and gid=nonnegative (get request "host_gid") in
    List.iter (fun k -> require (get request k=get context k) ("request/run mismatch: "^k))
      ((match named with None -> ["run_id"] | Some n ->
        require (equal (get request "publication") n.publication) "request/publication mismatch";[]) @ ["build_identity";"manifest_sha256"]);
    require (equal (get request "binding") (match named with None -> get context "execution_binding" | Some n -> n.binding)) "runtime binding mismatch";
    let synthesis_process=get context "synthesis_process" in
    (match named with Some _ -> () | None -> require (equal (get request "synthesis_process") synthesis_process &&
      get synthesis_process "outcome"=`String "exited" && get synthesis_process "code"=`Int 0 &&
      get synthesis_process "succeeded"=`Bool true) "synthesis process did not complete before linked analysis");
    let binding=match Identity_json.parse (Yojson.Safe.to_string (get request "binding")) with
      | Error _ -> invalid_arg "Linked timing flow: invalid runtime identity JSON"
      | Ok j -> (match Execution_transport.of_json j with Ok b -> b | Error e -> invalid_arg e) in
    (match binding with Execution_transport.Docker d when
      d.image_id=Hardcaml_asic_flow_domain.Backends.Librelane.Mapping.tested_image_id -> ()
      | _ -> invalid_arg "Linked timing flow: unsupported runtime/image binding");
    let check_docker_argv ~analysis argv =
      let args=List.map text (list argv) in
      match binding,args with
      | Execution_transport.Docker d,_::"run"::"--rm"::"--pull=never"::"--name"::name::_ ->
        let flags=match d.flags with Execution_transport.Isolated -> ["-I";"-B"] | Bytecode_disabled -> ["-B"] in
        let technology=text (get (get m "target") "technology") in
        let project=if analysis then historical_root else executed "project" in
        let prefix=Execution_transport.docker_command d ~project ~pdk_root ~name
          ~workdir:(executed "project") ?pdk:(if analysis then None else Some technology)
          ~workers:6 ~uid ~gid () in
        let payload=if analysis then [executed ((match named with None -> "timing-intent" | Some n -> n.directory)^"/timing_intent_runtime.py");executed ((match named with None -> "timing-intent" | Some n -> n.directory)^"/request.json")]
          else ["-m";"librelane";"--pdk-root";pdk_root;"--pdk";technology;"--manual-pdk";
            "--run-tag";"asic";"--force-run-dir";"runs/asic";"--to";"Yosys.Synthesis";
            "--jobs";"6";"-c";"OPENROAD_THREADS=6";"-c";"STA_THREADS=1";
            "-c";"KLAYOUT_DRC_THREADS=6";"-c";"KLAYOUT_XOR_THREADS=6";"src/config.json"] in
        require (args=prefix@flags@payload) "transport argv differs from qualified image/resources/executed source paths";args
      | _ -> invalid_arg "Linked timing flow: unexpected transport executable/operation" in
    (match named with Some _ -> () | None ->
      ignore (check_docker_argv ~analysis:false (get synthesis_process "argv"));
      require (get context "commands"=`List [get synthesis_process "argv"]) "synthesis command/process association differs");
    require (get request "producer_sha256"=`String (hash (member root ("project/"^producer)))) "producer changed";
    let producer_members=List.filter (fun r -> get r "path"=`String producer) (list (get m "files")) in
    require (List.length producer_members=1 && get (List.hd producer_members) "sha256"=get request "producer_sha256") "producer differs from identity-covered member";
    let result=retained (analysis "transport-result.json") in
    let launch=retained (analysis "transport.json") in
    require (get launch "request_sha256"=`String (hash (member root (analysis "request.json"))) &&
      get result "request_sha256"=get launch "request_sha256") "transport/request mismatch";
    require (equal (get launch "binding") (get request "binding")) "launch binding mismatch";
    ignore (check_docker_argv ~analysis:true (get launch "argv"));
    require (get launch "workers"=`Int 6 && get launch "sta_threads"=`Int 1) "unbounded transport resources";
    require (get request "helper_sha256"=`String (hash (member root (analysis "timing_intent_runtime.py")))) "retained runtime helper changed";
    let failed=get a "status"=`String "failed" in
    require (failed || get a "status"=`String "completed") "invalid companion status";
    exact result ["request_sha256";"succeeded";"outcome";"code";"cleanup_error"];
    let succeeded=match get result "succeeded" with `Bool b -> b | _ -> invalid_arg "Linked timing flow: invalid transport success Boolean" in
    let cleanup_ok=match get result "cleanup_error" with `Null -> true | `String s when s<>"" && not (String.contains s '\000') -> false
      | _ -> invalid_arg "Linked timing flow: invalid transport cleanup status" in
    let exited_zero=match get result "outcome",get result "code" with
      | `String "exited",`Int n when n>=0 && n<=255 -> n=0
      | `String "signaled",`Int n when n>0 && n<128 -> false
      | `String "cancelled",`Int (2|15) -> false
      | `String ("timed_out"|"failed"),`Null -> false
      | _ -> invalid_arg "Linked timing flow: invalid transport outcome/code" in
    let transport_ok=exited_zero && cleanup_ok in
    require (succeeded=transport_ok) "transport success contradicts process/cleanup status";
    require (failed || transport_ok) "hook completion without successful enclosing process";
    let common=["provenance",a;"source_digest",`String (hash a_path);
      "source_authentication",`String "hash_checked_retained_producer_assertions_not_authenticated_execution";
      "sources",`List (ref_ root (analysis "analysis.json")::sources);
      "verdict",`String "unavailable";"exhaustive_coverage",`String "unavailable"] in
    if failed then `Assoc (["status",`String "failed";"reason",`String "linked STA analysis did not complete verified publication; native coverage is not asserted"]@common)
    else (
      let h=retained (analysis "runtime-result.json") in
      List.iter (fun k -> require (get h k=get request k) ("helper/request mismatch: "^k))
        ((match named with None -> ["run_id"] | Some _ -> ["attempt_id";"occurrence_id"]) @ ["build_identity";"analysis_id"]);
      require (get h "schema"=`Int 1 && get h "status"=`String "completed") "helper not completed";
      let synth=get h "synthesis" in
      let config_path=bound (get synth "config") and state_path=bound (get synth "state") in
      (match named with Some n -> require (equal synth n.synthesis) "linked synthesis differs from exact publication sources" | None ->
      require (Filename.basename config_path="config.json" && Filename.basename state_path="state_out.json" &&
        Filename.dirname config_path=Filename.dirname state_path &&
        Filename.dirname (Filename.dirname config_path)="project/runs/asic") "synthesis config/state producer association differs");
      let config=retained config_path in
      require (get (get config "meta") "step"=`String "Yosys.Synthesis") "wrong synthesis producer";
      require (get (get config "meta") "librelane_version"=`String "3.1.0.dev3") "unsupported synthesis producer version";
      let state=retained state_path in
      let nl=bound (get synth "netlist") in
      require (get synth "state_netlist"=get state "nl") "synthesis state/netlist mismatch";
      let recorded=text (get state "nl") in
      (* Native state paths are historical labels after archive restore; the exact
         run-relative source and hash, not a current absolute path, is authority. *)
      require (recorded=executed nl) "netlist is not selected historical synthesis state member";
      let resolved_path=bound (get synth "resolved") in
      (match named with Some _ -> () | None -> require (resolved_path="project/runs/asic/resolved.json") "resolved configuration is not from selected synthesis run");
      let resolved=retained resolved_path in
      let effective=retained (bound (get h "effective_config")) in
      require (get resolved "PNR_SDC_FILE"=`String (executed "project/constraints/top.sdc")) "synthesis SDC historical path differs";
      require (get effective "STA_EXTRA_CORNER_TCL_FILE"=`String (executed (analysis "hook.tcl"))) "executed hook path differs from retained source";
      ignore (bound (ref_ root (analysis "hook.tcl")));
      let delta=retained (analysis "configuration-delta.json") in
      (match delta with `Assoc xs -> require
        (List.sort String.compare (List.map fst xs)=List.sort String.compare
          ["STA_CORNERS";"STA_THREADS";"OPENROAD_THREADS";"STA_EXTRA_CORNER_TCL_FILE"])
        "unexpected analysis delta keys" | _ -> invalid_arg "Linked timing flow: malformed analysis delta");
      List.iter (fun k -> let d=get delta k in
        require (equal (get d "requested") (get resolved k) && equal (get d "effective") (get effective k))
          ("analysis delta receipt mismatch: "^k))
        ["STA_CORNERS";"STA_THREADS";"OPENROAD_THREADS";"STA_EXTRA_CORNER_TCL_FILE"];
      (match effective with `Assoc fields -> List.iter (fun (k,v) ->
        if not (List.mem k ["meta";"STA_CORNERS";"STA_THREADS";"OPENROAD_THREADS";"STA_EXTRA_CORNER_TCL_FILE"])
        then require (equal (get resolved k) v) ("unexpected effective configuration delta: "^k)) fields
        | _ -> invalid_arg "Linked timing flow: malformed effective configuration");
      require (get effective "OPENROAD_THREADS"=`Int 1 && get effective "PNR_SDC_FILE"=get resolved "PNR_SDC_FILE") "invalid analysis thread/SDC delta";
      let corner=text (get resolved "DEFAULT_CORNER") in
      require (get h "requested_corners"=`List [`String corner] && get h "executed_corners"=`List [`String corner]) "requested/executed corners mismatch";
      require (get effective "STA_CORNERS"=`List [`String corner] && get effective "STA_THREADS"=`Int 1) "unbounded/effective corner config mismatch";
      let audits=list (get h "audits") in
      require (List.length audits=1) "missing/ambiguous executed corner";
      let views=List.map (fun audit ->
        require (get audit "corner"=`String corner) "wrong actual corner";
        let path=bound (get audit "audit") in
        let directory=Filename.dirname (member root path) in
        let view=match Timing_intent_evidence.inspect ~directory () with Ok v -> v | Error e -> invalid_arg ("Linked timing flow audit: "^e) in
        let provenance=get view "provenance" in
        require (get provenance "schema"=`Int 2 && get provenance "build_identity"=get request "build_identity" &&
          get provenance "audit_id"=get request "analysis_id" && get provenance "corner"=`String corner) "native audit context mismatch";
        require (get (get provenance "producer") "tool"=`String "OpenSTA" &&
          get (get provenance "producer") "version"=`String "2.7.0") "unsupported linked native producer/version";
        let version_result=retained (Filename.dirname path^"/version-process.json") in
        require (get version_result "argv"=`List [`String "sta";`String "-version"] &&
          get version_result "exit_code"=`Int 0 && get version_result "stderr"=`String "") "native version process did not complete";
        require (get version_result "stdout_sha256"=`String (hash (member root (Filename.dirname path^"/producer-version.txt")))) "native version source mismatch";
        let process=retained (Filename.dirname path^"/process-result.json") in
        let native_inputs=retained (Filename.dirname path^"/native-inputs.json") in
        let audit_relative=Filename.dirname path in
        let native_directory=text (get native_inputs "PT7_AUDIT_DIR") in
        require (native_directory=executed audit_relative &&
          get native_inputs "_CURRENT_CORNER_NAME"=`String corner &&
          get native_inputs "CURRENT_NL"=`String (native_directory^"/mapped.v") &&
          get native_inputs "_SDC_IN"=`String (native_directory^"/top.sdc") &&
          get native_inputs "PT7_RECEIPT"=`String (native_directory^"/receipt.json") &&
          get native_inputs "STA_EXTRA_CORNER_TCL_FILE"=get effective "STA_EXTRA_CORNER_TCL_FILE") "actual native input/corner receipt differs";
        let corner_script=get native_inputs "corner_script" in
        exact corner_script ["path";"sha256"];
        require (bound corner_script=audit_relative^"/librelane-corner.tcl" &&
          get native_inputs "PT7_CORNER_SCRIPT"=`String (executed (audit_relative^"/librelane-corner.tcl")))
          "executed corner script differs from retained source";
        let liberty_sources=List.filter (fun source -> get source "role"=`String "liberty") (list (get view "sources")) in
        let libraries=match get native_inputs "libraries" with `List xs -> xs
          | _ -> invalid_arg "Linked timing flow: actual Liberty association is missing/incomplete" in
        require (libraries<>[] && List.length libraries<=64 && List.length libraries=List.length liberty_sources)
          "actual Liberty association is missing/incomplete";
        let library_paths=List.map2 (fun association source ->
          exact association ["actual_path";"retained"];
          let retained_ref=get association "retained" in
          exact retained_ref ["path";"sha256"];
          let relative=audit_relative^"/"^text (get source "path") in
          require (bound retained_ref=relative && get retained_ref "sha256"=get source "sha256")
            "actual Liberty association differs from inner audit";
          let actual=historical_path (get association "actual_path") in
          require (actual=executed relative) "actual Liberty path differs from retained copy";actual) libraries liberty_sources in
        let raw_libraries=match get native_inputs "_CURRENT_CORNER_LIBS" with `String s -> s
          | _ -> invalid_arg "Linked timing flow: actual native Liberty list is not a string" in
        require (tcl_paths raw_libraries=library_paths)
          "actual native Liberty list differs from retained library associations";
        let args=list (get process "argv") in
        require (List.length args=6 && List.filteri (fun i _ -> i<5) args=
          [`String "sta";`String "-threads";`String "1";`String "-no_splash";`String "-exit"]) "unbounded or unexpected native process argv";
        require (text (List.nth args 5)=executed (analysis "driver.tcl")) "native process driver differs from retained historical source";
        let step_config=retained (analysis "corners/config.json") in
        require (get (get step_config "meta") "step"=`String "OpenROAD.STAPrePNR") "wrong linked native step";
        require (get (get step_config "meta") "librelane_version"=`String "3.1.0.dev3") "unsupported linked step version";
        List.iter (fun k -> require (equal (get step_config k) (get effective k)) ("native step effective config mismatch: "^k))
          ["STA_CORNERS";"STA_THREADS";"OPENROAD_THREADS";"STA_EXTRA_CORNER_TCL_FILE";"PNR_SDC_FILE"];
        let native_in=retained (analysis "corners/state_in.json") in
        require (get native_in "nl"=get state "nl") "native step loaded a different synthesis state/netlist";
        (match named with None -> () | Some _ -> require (equal native_in state) "native step input differs from exact published State");
        ignore (retained (analysis "corners/state_out.json"));
        require (List.mem (analysis "corners/runtime.txt") paths) "native step did not finish after hook";
        List.iter (fun source ->
          let role=get source "role" in
          if role=`String "netlist" then require (get source "sha256"=get (get synth "netlist") "sha256") "native/synthesis netlist hash mismatch";
          if role=`String "sdc" then require (get source "sha256"=`String (hash (member root "project/constraints/top.sdc"))) "native/emitted SDC mismatch";
          if role=`String "native_producer" then require (get source "sha256"=get request "producer_sha256") "native/emitted producer mismatch";
          if role=`String "driver" then require (get source "sha256"=`String (hash (member root (analysis "driver.tcl")))) "native/retained executed driver mismatch";
          let relative=Filename.dirname path^"/"^text (get source "path") in
          ignore (bound (`Assoc ["path",`String relative;"sha256",get source "sha256"]))) (list (get view "sources"));
        view) audits in
      List.iter (fun source -> ignore (check_ref root source)) sources;
      `Assoc (["status",`String "completed";"corners",`List views;
        "configuration_delta",delta;"original_corners",get resolved "STA_CORNERS";
        "synthesis",synth;"requested_corners",get h "requested_corners";
        "executed_corners",get h "executed_corners"]@common)))
let inspect ~run_dir () = protect (fun () ->
  let root=Unix.realpath run_dir in
  let record=member root "run.json" in
  let before=hash record in
  let result=inspect_impl ~run_dir:root ~context:(read record) () in
  require (hash (member root "run.json")=before) "run record changed while inspecting";
  result)
let source_files ~run_dir ~context () = protect (fun () ->
  let view=inspect_impl ~run_dir ~context () in list (get view "sources"))
