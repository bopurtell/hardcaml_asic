(* Terminal, supplementary evidence. Never a checkpoint or execution boundary. *)
module J = Identity_json
module E = Evidence_json

let checked = function
  | Ok x -> x
  | Error e -> invalid_arg e
;;

let codec = function
  | Ok x -> x
  | Error e -> invalid_arg (J.error_message e)
;;

let str x = codec (J.string x)
let obj x = codec (J.object_ x)
let int x = codec (J.integer (string_of_int x))
let num x = codec (J.float x)

let get j k =
  match j with
  | J.Object xs -> Option.value (List.assoc_opt k xs) ~default:J.null
  | _ -> J.null
;;

let text = function
  | J.String s -> s
  | _ -> invalid_arg "timing analysis: expected string"
;;

let array = function
  | J.Array xs -> xs
  | _ -> invalid_arg "timing analysis: expected array"
;;

let require b s = if not b then invalid_arg ("timing analysis: " ^ s)
let path execution = execution ^ ".timing-analysis.json"
let within root p = p = root || String.starts_with ~prefix:(root ^ "/") p

let absent p =
  try
    ignore (Unix.lstat p);
    false
  with
  | Unix.Unix_error (Unix.ENOENT, _, _) -> true
;;

let same a b =
  a.Unix.st_dev = b.Unix.st_dev
  && a.st_ino = b.st_ino
  && a.st_size = b.st_size
  && a.st_mtime = b.st_mtime
  && a.st_ctime = b.st_ctime
;;

type reader =
  { mutable bytes : int
  ; mutable report_bytes : int
  ; mutable sources : (string * string) list
  }

let reader () = { bytes = 0; report_bytes = 0; sources = [] }

(* Native reports are verbose, not metadata. Bound disk work separately and never
   materialize their contents (or a list proportional to their line count). *)
let report r ?root ?line p =
  require (List.length r.sources < 512) "source count bound exceeded";
  require (Unix.realpath p = p) "noncanonical source or symlink";
  Option.iter (fun root -> require (within root p) "source escapes retained root") root;
  let fd = Unix.openfile p [ Unix.O_RDONLY; Unix.O_NONBLOCK ] 0 in
  let ic = Unix.in_channel_of_descr fd in
  let hash = Cryptokit.Hash.sha256 () in
  Fun.protect
    ~finally:(fun () ->
      close_in_noerr ic;
      hash#wipe)
    (fun () ->
      let before = Unix.fstat fd in
      require
        (before.st_kind = Unix.S_REG && before.st_size <= 32 * 1024 * 1024)
        "report source kind/size";
      r.report_bytes <- r.report_bytes + before.st_size;
      require (r.report_bytes <= 256 * 1024 * 1024) "aggregate report byte bound exceeded";
      let chunk = Bytes.create 65536 and pending = Buffer.create 256 in
      let count = ref 0 and number = ref 1 in
      let emit () =
        Option.iter (fun f -> f !number (Buffer.contents pending)) line;
        Buffer.clear pending;
        incr number
      in
      let rec loop () =
        let n = input ic chunk 0 (Bytes.length chunk) in
        if n <> 0 then (
          count := !count + n;
          require (!count <= before.st_size) "report source grew during capture";
          hash#add_substring chunk 0 n;
          (match line with
           | None -> ()
           | Some _ ->
             for i = 0 to n - 1 do
               let c = Bytes.get chunk i in
               if c = '\n'
               then emit ()
               else (
                 require (Buffer.length pending < 16384) "report line byte bound exceeded";
                 require (Char.code c >= 32 || c = '\t' || c = '\r') "binary timing log";
                 Buffer.add_char pending c)
             done);
          loop ())
      in
      loop ();
      if Buffer.length pending > 0 then emit ();
      require
        (!count = before.st_size
         && same before (Unix.fstat fd) && same before (Unix.stat p) && Unix.realpath p = p)
        "source changed during capture";
      let digest = Cryptokit.transform_string (Cryptokit.Hexa.encode ()) hash#result in
      r.sources <- (p, digest) :: r.sources)
;;

let is_report p = List.mem (Filename.basename p) [ "sta.log"; "max.rpt"; "min.rpt" ]

let read r ?root ?(limit = 32 * 1024 * 1024) p =
  require (List.length r.sources < 512) "source count bound exceeded";
  require (Unix.realpath p = p) "noncanonical source or symlink";
  Option.iter (fun root -> require (within root p) "source escapes retained root") root;
  let fd = Unix.openfile p [ Unix.O_RDONLY; Unix.O_NONBLOCK ] 0 in
  let ic = Unix.in_channel_of_descr fd in
  let raw =
    Fun.protect
      ~finally:(fun () -> close_in_noerr ic)
      (fun () ->
        let before = Unix.fstat fd in
        require
          (before.st_kind = Unix.S_REG && before.st_size <= limit)
          "source kind/size";
        r.bytes <- r.bytes + before.st_size;
        require (r.bytes <= 64 * 1024 * 1024) "aggregate byte bound exceeded";
        let raw = really_input_string ic before.st_size in
        require
          (same before (Unix.fstat fd) && same before (Unix.stat p) && Unix.realpath p = p)
          "source changed during capture";
        raw)
  in
  let hash = J.sha256_bytes raw in
  r.sources <- (p, hash) :: r.sources;
  raw
;;

let json r ?root p = codec (J.parse (read r ?root p))

let evidence r ~root p =
  E.value (checked (E.parse ~reject_duplicates:true (read r ~root p)))
;;

let source r p = obj [ "path", str p; "sha256", str (List.assoc p r.sources) ]

let recheck r =
  let verification = reader () in
  List.iter
    (fun (p, h) ->
      let actual =
        if is_report p then (report verification p; List.assoc p verification.sources)
        else J.sha256_bytes (read verification p)
      in
      require
        (actual = h && Unix.realpath p = p)
        "retained source changed")
    r.sources
;;

let runtime raw =
  let s = String.trim raw in
  let re =
    Str.regexp
      "^\\([0-9][0-9]+\\):\\([0-9][0-9]\\):\\([0-9][0-9]\\)\\.\\([0-9][0-9][0-9]\\)$"
  in
  require (String.length s <= 32 && Str.string_match re s 0) "malformed native runtime";
  let h = float_of_string (Str.matched_group 1 s)
  and m = int_of_string (Str.matched_group 2 s)
  and sec = int_of_string (Str.matched_group 3 s)
  and ms = int_of_string (Str.matched_group 4 s) in
  require (m < 60 && sec < 60 && Float.is_finite h) "invalid native runtime";
  (h *. 3600.) +. float_of_int ((m * 60) + sec) +. (float_of_int ms /. 1000.)
;;

let names dir =
  let d = Unix.opendir dir in
  Fun.protect
    ~finally:(fun () -> Unix.closedir d)
    (fun () ->
      let rec loop n xs =
        match Unix.readdir d with
        | "." | ".." -> loop n xs
        | s ->
          require (n < 1024) "directory entry bound exceeded";
          loop (n + 1) (s :: xs)
        | exception End_of_file -> List.sort String.compare xs
      in
      loop 0 [])
;;

let native_runtime_seconds = runtime

let safe_component s =
  s <> ""
  && s <> "."
  && s <> ".."
  && Filename.basename s = s
  && not (String.contains s '\000')
;;

let finite = function
  | `Int n -> Some (float_of_int n)
  | `Intlit s ->
    (match float_of_string_opt s with
     | Some f when Float.is_finite f -> Some f
     | _ -> None)
  | `Float f when Float.is_finite f -> Some f
  | _ -> None
;;

let efield j k =
  match j with
  | `Assoc xs -> Option.value (List.assoc_opt k xs) ~default:`Null
  | _ -> `Null
;;

let inventory r =
  J.array
    (List.sort_uniq compare r.sources
     |> List.map (fun (p, h) -> obj [ "path", str p; "sha256", str h ]))
;;

let unavailable reason =
  obj
    [ "status", str "unavailable"
    ; "reason", str reason
    ; "duration", J.null
    ; "producer", J.null
    ; "corners", J.array []
    ; "metrics", J.array []
    ; "timing_goal", str "unknown"
    ]
;;

let collect r run_dir =
  require (Unix.realpath run_dir = run_dir) "noncanonical run directory";
  let candidates =
    names run_dir
    |> List.filter_map (fun name ->
      match String.index_opt name '-' with
      | None -> None
      | Some i ->
        let ordinal = String.sub name 0 i in
        let suffix = String.sub name (i + 1) (String.length name - i - 1) in
        if suffix = "openroad-stapostpnr"
           || String.starts_with ~prefix:"openroad-stapostpnr-" suffix
        then (
          require
            (ordinal <> ""
             && String.for_all
                  (function
                    | '0' .. '9' -> true
                    | _ -> false)
                  ordinal)
            "invalid producer ordinal";
          Some (int_of_string ordinal, Filename.concat run_dir name, suffix))
        else None)
  in
  match candidates with
  | [] -> unavailable "post_pnr_sta_not_observed"
  | _ :: _ :: _ -> unavailable "ambiguous_post_pnr_producer"
  | [ (ordinal, dir, suffix) ] ->
    require (ordinal > 0) "invalid producer ordinal";
    let config_path = dir ^ "/config.json" in
    let config = json r ~root:run_dir config_path in
    let meta = get config "meta" in
    require
      (get meta "step" = str "OpenROAD.STAPostPNR"
       && get meta "librelane_version" = str "3.1.0.dev3")
      "unsupported producer metadata/version";
    let id =
      "OpenROAD.STAPostPNR"
      ^ String.sub
          suffix
          (String.length "openroad-stapostpnr")
          (String.length suffix - String.length "openroad-stapostpnr")
    in
    let suffix =
      String.sub
        suffix
        (String.length "openroad-stapostpnr")
        (String.length suffix - String.length "openroad-stapostpnr")
    in
    require
      (suffix = ""
       || (String.length suffix > 1
           && suffix.[0] = '-'
           && String.for_all
                (function
                  | '0' .. '9' -> true
                  | _ -> false)
                (String.sub suffix 1 (String.length suffix - 1))))
      "invalid producer occurrence suffix";
    let configured = array (get config "STA_CORNERS") |> List.map text in
    require
      (configured <> []
       && List.length configured <= 64
       && List.for_all safe_component configured
       && List.length configured = List.length (List.sort_uniq String.compare configured)
      )
      "invalid configured corners";
    let input = evidence r ~root:run_dir (dir ^ "/state_in.json") in
    let output_path = dir ^ "/state_out.json" in
    let output = evidence r ~root:run_dir output_path in
    require
      (match input, output with
       | `Assoc _, `Assoc _ -> true
       | _ -> false)
      "invalid native state";
    require
      (match efield output "metrics", efield output "lib" with
       | `Assoc _, `Assoc _ -> true
       | _ -> false)
      "invalid completed STA state";
    let seconds = runtime (read r ~root:run_dir (dir ^ "/runtime.txt")) in
    let metrics = ref [] in
    let corners =
      List.map
        (fun corner ->
          let corner_dir = dir ^ "/" ^ corner in
          let log_path = corner_dir ^ "/sta.log" in
          if absent log_path
          then
            obj
              [ "corner", str corner
              ; "status", str "missing_or_equivalent"
              ; "setup_goal", str "unknown"
              ; "hold_goal", str "unknown"
              ]
          else (
            let keys =
              List.concat_map
                (fun mode ->
                  List.map
                    (fun suffix -> "timing__" ^ mode ^ suffix ^ "__corner:" ^ corner)
                    [ "__ws"; "_vio__count" ])
                [ "setup"; "hold" ]
            in
            let observed = Hashtbl.create 4 in
            let words = Str.regexp "[ \t]+" in
            report r ~root:run_dir
              ~line:(fun number line ->
                match Str.split words line with
                | tag :: key :: rest
                  when List.mem key keys && String.starts_with ~prefix:"%OL_METRIC" tag ->
                  (* Saturate duplicates, including malformed repeats. Never let a
                     valid prefix hide conflicting evidence at the end of a log. *)
                  let value = match rest with [ v ] -> v | _ -> "" in
                  let prior = Option.value (Hashtbl.find_opt observed key) ~default:[] in
                  if List.length prior < 2
                  then Hashtbl.replace observed key ((number, tag, value) :: prior)
                | _ -> ())
              log_path;
            let lib = efield (efield output "lib") corner in
            let unit_ok =
              match lib with
              | `String p when Filename.dirname p = corner_dir && not (absent p) ->
                let raw = read r ~root:run_dir p in
                (match Liberty_unit_evidence.parse raw with
                 | [ (_, "1ns", _) ], [] -> true
                 | _ -> false)
              | _ -> false
            in
            List.iter
              (fun name ->
                let p = corner_dir ^ "/" ^ name in
                if not (absent p) then report r ~root:run_dir p)
              [ "max.rpt"; "min.rpt" ];
            let metric mode kind =
              let key =
                "timing__"
                ^ mode
                ^ (if kind = "violations" then "_vio__count" else "__ws")
                ^ "__corner:"
                ^ corner
              in
              let tokens =
                Option.value (Hashtbl.find_opt observed key) ~default:[]
              in
              let raw_value = efield (efield output "metrics") key in
              let value =
                match tokens with
                | [ (_, tag, v) ] ->
                  (match float_of_string_opt v, finite raw_value with
                   | Some f, Some state
                     when Float.is_finite f
                          && abs_float f < 1e30
                          && f = state
                          && (kind = "violations" || tag = "%OL_METRIC_F")
                          && (kind <> "violations"
                              || (tag = "%OL_METRIC_I"
                                  && f >= 0.
                                  && f <= 9007199254740991.
                                  && floor f = f))
                          && (kind = "violations" || unit_ok) -> Some f
                   | _ -> None)
                | _ -> None
              in
              let supplied =
                obj
                  [ "key", str (mode ^ "_" ^ kind)
                  ; "raw_key", str key
                  ; "corner", str corner
                  ; ( "units_source"
                    , if unit_ok && kind <> "violations"
                      then (
                        match lib with
                        | `String p -> source r p
                        | _ -> J.null)
                      else J.null )
                  ; ( "value"
                    , match value with
                      | None -> J.null
                      | Some f -> num f )
                  ; ( "unit"
                    , if kind = "violations"
                      then str "count"
                      else if unit_ok
                      then str "ns"
                      else J.null )
                  ; ( "availability"
                    , str (if value = None then "unavailable" else "available") )
                  ; ( "reason"
                    , if value = None
                      then str "missing_conflicting_nonfinite_or_unknown_unit"
                      else J.null )
                  ; ( "source"
                    , obj
                        [ "file", source r log_path
                        ; "state", source r output_path
                        ; ( "line"
                          , match tokens with
                            | [ (line, _, _) ] -> int line
                            | _ -> J.null )
                        ] )
                  ]
              in
              metrics := supplied :: !metrics;
              value
            in
            let verdict mode =
              match metric mode "slack", metric mode "violations" with
              | Some slack, Some count ->
                if slack < 0. || count > 0. then "fail" else "pass"
              | Some slack, _ when slack < 0. -> "fail"
              | _, Some count when count > 0. -> "fail"
              | _ -> "unknown"
            in
            let setup = verdict "setup"
            and hold = verdict "hold" in
            obj
              [ "corner", str corner
              ; "status", str "measured"
              ; "setup_goal", str setup
              ; "hold_goal", str hold
              ]))
        configured
    in
    let goals =
      List.concat_map (fun c -> [ get c "setup_goal"; get c "hold_goal" ]) corners
    in
    let goal =
      if List.mem (str "fail") goals
      then "fail"
      else if List.for_all (( = ) (str "pass")) goals
      then "pass"
      else "unknown"
    in
    obj
      [ "status", str "completed"
      ; "reason", J.null
      ; ( "duration"
        , obj
            [ "seconds", num seconds
            ; "basis", str "librelane_step_wall_clock"
            ; "precision_seconds", num 0.001
            ; "source", source r (dir ^ "/runtime.txt")
            ] )
      ; ( "producer"
        , obj
            [ "class_id", str "OpenROAD.STAPostPNR"
            ; "step_id", str id
            ; "ordinal", int ordinal
            ; "directory", str dir
            ; "run_dir", str run_dir
            ; "config", source r config_path
            ] )
      ; "corners", J.array corners
      ; "metrics", J.array (List.rev !metrics)
      ; "timing_goal", str goal
      ]
;;

let capture ~execution ~transport_records =
  let r = reader () in
  let execution_raw = json r execution in
  require
    (List.mem
       (get execution_raw "status")
       (List.map str [ "running"; "completed"; "failed"; "interrupted" ]))
    "execution is not terminal";
  let attempt = text (get execution_raw "attempt_id") in
  require (safe_component attempt) "invalid execution attempt";
  let plan = checked (Execution_plan.Stage_plan.of_json (get execution_raw "plan")) in
  let producer_stage =
    if Execution_plan.Stage_plan.schema_version plan = 4
    then Execution_plan.Stage.Timing
    else Execution_plan.Stage.Signoff
  in
  let stage = Execution_plan.Stage.to_string producer_stage in
  let requested = List.mem producer_stage (Execution_plan.Stage_plan.sequence plan) in
  if not requested
  then None
  else (
    let selected =
      List.filter_map
        (fun (launch, terminal) ->
          let launch_json = json r launch in
          let request_path = text (get launch_json "request_path") in
          let request =
            checked (Backend_protocol.request_of_json (json r request_path))
          in
          if request.operation = Backend_protocol.Execute_segment
             && request.boundary = producer_stage
          then Some (launch_json, terminal, request)
          else None)
        transport_records
    in
    let detail, context =
      match selected with
      | [] -> unavailable "signoff_backend_not_invoked", []
      | [ (launch, terminal, request) ] ->
        let terminal_json = json r terminal in
        require
          (get launch "request_sha256" = str (Backend_protocol.request_identity request)
           && get terminal_json "request_sha256" = get launch "request_sha256"
           && get terminal_json "request_id" = str request.request_id)
          "transport correlation mismatch";
        let run_dir = request.project ^ "/src/runs/" ^ request.tag in
        let detail =
          try
            require
              (get (get (get launch "binding") "image_identity") "image_id"
               = str Hardcaml_asic_flow_domain.Backends.Librelane.Mapping.tested_image_id
              )
              "unsupported execution image";
            require
              (get launch "fresh_run_dir" = str run_dir)
              "run directory was not fresh before invocation";
            if absent run_dir
            then unavailable "post_pnr_sta_not_observed"
            else collect r run_dir
          with
          | Invalid_argument s | Failure s | Sys_error s -> unavailable s
          | Unix.Unix_error (e, _, _) -> unavailable (Unix.error_message e)
          | End_of_file -> unavailable "truncated_native_source"
        in
        ( detail
        , [ "request_id", str request.request_id
          ; "request_sha256", str (Backend_protocol.request_identity request)
          ; "request", Backend_protocol.request_to_json request
          ; "transport_terminal", str terminal
          ] )
      | _ -> unavailable "ambiguous_signoff_invocation", []
    in
    recheck r;
    let fields =
      match detail with
      | J.Object xs -> xs
      | _ -> assert false
    in
    let payload =
      obj
        ([ "contract", str "hardcaml-asic.timing-analysis"
         ; "schema_version", int (if stage = "timing" then 2 else 1)
         ; "attempt_id", str attempt
         ; "occurrence_id", str (attempt ^ "/implementation." ^ stage)
         ; "analysis_id", str (attempt ^ "/post-pnr-sta")
         ; "stage", str stage
         ; "execution_file", str execution
         ; "execution_sha256", str (List.assoc execution r.sources)
         ; "enclosing_status", get execution_raw "status"
         ; ( "evidence_scope"
           , str
               (if stage = "timing"
                then "immutable_timing_execution_source_not_checkpoint"
                else "terminal_supplementary_not_checkpoint") )
         ; "native_grammar", str "librelane-3.1.0.dev3-step-output-runtime-v1"
         ; "inventory", inventory r
         ]
         @ context
         @ fields)
    in
    let digest = J.identity payload in
    let result =
      obj
        ((match payload with
          | J.Object xs -> xs
          | _ -> assert false)
         @ [ "source_digest", str digest ])
    in
    Some result)
;;

let publish ~execution ~transport_records =
  Option.iter
    (fun result ->
      let bytes = J.canonical result ^ "\n" in
      require (String.length bytes <= 1024 * 1024) "observation byte bound exceeded";
      let destination = path execution in
      let temporary =
        Filename.temp_file ~temp_dir:(Filename.dirname execution) ".timing-analysis-" ".tmp"
      in
      Fun.protect
        ~finally:(fun () -> if Sys.file_exists temporary then Unix.unlink temporary)
        (fun () ->
          let oc = open_out_bin temporary in
          Fun.protect
            ~finally:(fun () -> close_out_noerr oc)
            (fun () -> output_string oc bytes);
          Unix.link temporary destination))
    (capture ~execution ~transport_records)
;;

(* Metadata is immutable; native sources are independently revalidated by inspection.
   Reports preserve the completed observation even if a report was later removed. *)
let rec load ~execution ~execution_raw =
  let p = path execution in
  if absent p
  then (
    let snapshot = execution ^ ".timing-source" in
    if absent snapshot
    then None
    else (
      let r = reader () in
      let frozen_raw = read r snapshot in
      let current = codec (J.parse execution_raw)
      and frozen = codec (J.parse frozen_raw) in
      List.iter
        (fun key ->
          require (get current key = get frozen key) ("timing prefix " ^ key ^ " mismatch"))
        [ "attempt_id"; "plan" ];
      let select field key value j =
        List.filter (fun x -> get x key = str value) (array (get j field))
      in
      List.iter
        (fun (field, key, value) ->
          let a = select field key value current
          and b = select field key value frozen in
          require (List.length a = 1 && a = b) "timing prefix source changed")
        [ "occurrences", "action_key", "implementation.timing"
        ; "artifacts", "boundary", "timing"
        ];
      let result = load ~execution:snapshot ~execution_raw:frozen_raw in
      recheck r;
      result))
  else (
    let r = reader () in
    let raw = read r ~limit:(1024 * 1024) p in
    require (String.length raw <= 1024 * 1024) "observation byte bound exceeded";
    let j = codec (J.parse raw) in
    require
      (get j "contract" = str "hardcaml-asic.timing-analysis"
       && ((get j "schema_version" = int 1 && get j "stage" = str "signoff")
           || (get j "schema_version" = int 2 && get j "stage" = str "timing")))
      "unsupported observation";
    let payload =
      match j with
      | J.Object xs -> obj (List.remove_assoc "source_digest" xs)
      | _ -> assert false
    in
    require
      (get j "source_digest" = str (J.identity payload))
      "observation digest mismatch";
    require
      (get j "execution_file" = str execution
       && get j "execution_sha256" = str (J.sha256_bytes execution_raw))
      "observation execution mismatch";
    let record = codec (J.parse execution_raw) in
    require
      (get j "attempt_id" = get record "attempt_id"
       && get j "enclosing_status" = get record "status")
      "observation attempt/outcome mismatch";
    let attempt = text (get record "attempt_id") in
    require
      (get j "occurrence_id" = str (attempt ^ "/implementation." ^ text (get j "stage"))
       && get j "analysis_id" = str (attempt ^ "/post-pnr-sta")
       && List.mem (get j "stage") [ str "signoff"; str "timing" ])
      "observation analysis identity mismatch";
    let refs = array (get j "inventory") in
    require
      (List.length refs <= 512
       && List.length (List.sort_uniq compare refs) = List.length refs)
      "invalid observation inventory";
    Some j)
;;

let validate_sources j =
  let r = reader () in
  List.iter
    (fun ref ->
      let p = text (get ref "path") in
      let hash =
        if is_report p then (report r p; List.assoc p r.sources)
        else J.sha256_bytes (read r p)
      in
      require
        (hash = text (get ref "sha256"))
        "observation source changed")
    (array (get j "inventory"));
  recheck r
;;

let load_file ?expected_record execution =
  let r = reader () in
  let execution_raw = read r execution in
  Option.iter
    (fun expected ->
      require
        (codec (J.parse execution_raw) = expected)
        "execution changed between report and observation")
    expected_record;
  let result = load ~execution ~execution_raw in
  recheck r;
  result
;;

let reinspect ~execution ~expected_record =
  let original = match load_file ~expected_record execution with
    | Some a -> a
    | None -> invalid_arg "timing reinspection requires a retained observation"
  in
  validate_sources original;
  let frozen = text (get original "execution_file") in
  let terminal = text (get original "transport_terminal") in
  let request_id = text (get original "request_id") in
  require (safe_component request_id) "invalid retained request identity";
  let launch = Filename.dirname frozen ^ "/" ^ request_id ^ "-transport.json" in
  List.iter (fun p ->
    require (List.exists (fun ref -> get ref "path" = str p) (array (get original "inventory")))
      "reinspection transport absent from original inventory") [launch; terminal];
  let refreshed = match capture ~execution:frozen ~transport_records:[launch, terminal] with
    | Some a -> a
    | None -> invalid_arg "timing reinspection did not select a timing producer"
  in
  List.iter (fun key -> require (get refreshed key = get original key)
    ("reinspection association mismatch: " ^ key))
    ["execution_file"; "execution_sha256"; "attempt_id"; "occurrence_id";
     "analysis_id"; "stage"; "request_id"; "request_sha256"; "request"; "transport_terminal"];
  validate_sources refreshed;
  original, refreshed
;;
