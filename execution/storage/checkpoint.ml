module J = Identity_json
module Stage = Hardcaml_asic.Flow.Stage
type schema = Schema_1 | Schema_2 | Schema_3
type file_record = { sha256 : string; bytes : string }
type t =
  { schema : schema; identity : string; location : string; boundary : Stage.t
  ; restart_targets : Stage.t list; compatibility : J.t
  ; files : (string * file_record) list; external_files : (string * file_record) list
  ; metrics : J.t; raw : J.t
  }
exception Invalid of string
let fail s = raise (Invalid s)
let protect f = try Ok (f ()) with
  | Invalid s -> Error s
  | Sys_error s -> Error s
  | Unix.Unix_error (e, op, path) -> Error (op ^ ": " ^ path ^ ": " ^ Unix.error_message e)
let codec = function Ok x -> x | Error e -> fail (J.error_message e)
let str s = codec (J.string s)
let obj xs = codec (J.object_ xs)
let fields = function J.Object xs -> xs | _ -> fail "expected object"
let get j name = match List.assoc_opt name (fields j) with Some x -> x | None -> fail ("missing " ^ name)
let string = function J.String s -> s | _ -> fail "expected string"
let equal a b = J.canonical a = J.canonical b
(* Compatibility is the provider's value equality, not an identity digest.
   Python compares finite integers/floats/bools by numeric value here. Identity
   verification above/below still preserves their distinct canonical bytes. *)
let rec compatibility_equal a b =
  let numeric = function
    | J.Integer s -> Some (`Int (Z.of_string s))
    | J.Bool b -> Some (`Int (if b then Z.one else Z.zero))
    | J.Float f -> Some (`Float f)
    | _ -> None in
  match numeric a, numeric b with
  | Some (`Int a), Some (`Int b) -> Z.equal a b
  | Some (`Float a), Some (`Float b) -> a = b
  | Some (`Int z), Some (`Float f) | Some (`Float f), Some (`Int z) ->
    Float.floor f = f && Z.equal z (Z.of_float f)
  | _ -> match a, b with
    | J.Object xs, J.Object ys ->
      List.length xs = List.length ys && List.for_all (fun (k, v) ->
        match List.assoc_opt k ys with Some w -> compatibility_equal v w | None -> false) xs
    | J.Array xs, J.Array ys -> List.length xs = List.length ys && List.for_all2 compatibility_equal xs ys
    | _ -> equal a b
let path_string j = let s = string j in
  if s = "" || String.contains s '\000' then fail "invalid path"; s
let normalize_path p =
  if p = "" || String.contains p '\000' then fail "invalid path";
  let p = if Filename.is_relative p then Filename.concat (Sys.getcwd ()) p else p in
  (* pathlib.absolute retains .. but removes empty and dot components. *)
  "/" ^ String.concat "/" (List.filter (fun s -> s <> "" && s <> ".") (String.split_on_char '/' p))
let absolute_path p = protect (fun () -> normalize_path p)
let absolute p = not (Filename.is_relative p) && p <> "" && not (String.contains p '\000')
let sha s = String.length s = 64 && String.for_all (function '0'..'9' | 'a'..'f' -> true | _ -> false) s
let record j =
  ignore (codec (J.exact_object j ~fields:["sha256"; "bytes"]));
  let sha256 = string (get j "sha256") in
  let bytes = match get j "bytes" with J.Integer s when s.[0] <> '-' -> s | _ -> fail "invalid file size" in
  if not (sha sha256) then fail "invalid artifact digest";
  { sha256; bytes }
let inventory j = List.map (fun (n, v) -> n, record v) (fields j)
let stage s = match List.find_opt (fun x -> Stage.to_string x = s) Stage.all with
  | Some x -> x | None -> fail "invalid boundary capability"
let targets schema boundary = match schema, boundary with
  | Schema_1, Stage.Synthesis -> [Stage.Floorplan; Stage.Placement]
  | Schema_1, _ -> []
  | Schema_3, Stage.Synthesis -> [Stage.Floorplan]
  | Schema_3, Stage.Timing -> []
  | Schema_3, Stage.Signoff -> fail "segmented SRAM Signoff checkpoints are unsupported"
  | (Schema_2 | Schema_3), _ -> (Stage.capability boundary).restart_targets
(* Schema3 is specifically sram_segmented1, not a generic checkpoint upgrade.
   Evidence paths are copied/rebound by the publisher; producer metadata stays
   unchanged. Timing is terminal for this capability, never a Signoff handoff. *)
let macro_evidence ~boundary raw = protect (fun () ->
  let entries=match raw with J.Array xs -> xs | _ -> fail "macro evidence array required" in
  let expected=match boundary with Stage.Synthesis | Stage.Floorplan -> 0 | Stage.Signoff ->
    fail "segmented SRAM Signoff is unsupported" | _ -> 1 in
  if List.length entries <> expected then fail "missing/duplicate transitive macro PDN evidence";
  List.iter (fun entry ->
    ignore (codec (J.exact_object entry ~fields:["kind";"path";"sha256";"bytes";"producer_boundary";"producer_tag"]));
    if get entry "kind" <> str "macro_power_geometry" || get entry "producer_boundary" <> str "placement" then
      fail "unsupported macro evidence kind/producer";
    let tag=string (get entry "producer_tag") in
    if tag="" || tag="." || tag=".." || String.contains tag '/' || String.contains tag '\000' then fail "invalid macro evidence producer tag";
    if not (absolute (path_string (get entry "path"))) then fail "absolute macro evidence path required";
    ignore (record (obj ["sha256",get entry "sha256";"bytes",get entry "bytes"]))) entries;
  entries)
let decode raw =
  let schema = match get raw "schema_version" with
    | J.Integer "1" -> Schema_1 | J.Integer "2" -> Schema_2 | J.Integer "3" -> Schema_3
    | _ -> fail "unsupported checkpoint manifest" in
  let names = ["schema_version"; "identity"; "location"; "boundary"; "restart_supported";
               "compatibility"; "files"; "external"; "metrics"; "acceptance"] in
  ignore (codec (J.exact_object raw ~fields:(names @ (match schema with Schema_1 -> []
    | Schema_2 -> ["restart_targets"; "tested_image_id"; "closure_scope"]
    | Schema_3 -> ["restart_targets"; "tested_image_id"; "closure_scope"; "macro_profile"; "macro_evidence"]))));
  let identity = string (get raw "identity") in
  if identity <> J.identity (codec (J.unsigned raw ~field:"identity")) then fail "checkpoint identity/location mismatch";
  let location = path_string (get raw "location") in
  if not (absolute location) then fail "checkpoint identity/location mismatch";
  let boundary = stage (string (get raw "boundary")) in
  if schema = Schema_1 && not (List.mem boundary [Stage.Synthesis; Stage.Floorplan; Stage.Placement])
  then fail "invalid boundary capability";
  let restart_targets =
    if schema = Schema_2
       && boundary = Stage.Routing
       && get raw "restart_targets" = J.array [ str "signoff" ]
    then [ Stage.Signoff ]
    else targets schema boundary
  in
  if not (equal (get raw "restart_supported") (J.bool (restart_targets <> [])))
     || get raw "acceptance" <> str "not_evaluated" then fail "invalid boundary capability";
   if schema <> Schema_1 &&
     (not (equal (get raw "restart_targets") (J.array (List.map (fun s -> str (Stage.to_string s)) restart_targets)))
      || get raw "tested_image_id" <> str (Stage.capability boundary).tested_image_id
      || get raw "closure_scope" <> str "path_bound") then fail "invalid tested restart/closure capability";
   let files = inventory (get raw "files") and external_files = inventory (get raw "external") in
   if schema=Schema_3 then (
     if get raw "macro_profile" <> str Macro_contract.segmented_profile then fail "unsupported macro checkpoint profile";
     let entries=match macro_evidence ~boundary (get raw "macro_evidence") with Ok xs -> xs | Error e -> fail e in
     List.iter (fun e ->
       let p=path_string (get e "path") in
       if not (String.starts_with ~prefix:(location ^ "/views/") p) then fail "macro evidence escapes checkpoint";
       let relative=String.sub p (String.length location+1) (String.length p-String.length location-1) in
       if List.assoc_opt relative files <> Some (record (obj ["sha256",get e "sha256";"bytes",get e "bytes"])) then
         fail "macro evidence is not bound by checkpoint inventory") entries);
  if not (List.for_all (fun n -> List.mem_assoc n files) ["state.json"; "resolved.json"; "closure.json"; "segment.json"])
  then fail "incomplete checkpoint metadata";
  List.iter (fun (name, _) ->
    if name = "" || not (Filename.is_relative name) || String.contains name '\000'
       || List.mem ".." (String.split_on_char '/' name) then fail "invalid inventory path") files;
  if external_files = [] || not (List.for_all (fun (n, _) -> absolute n) external_files)
  then fail "missing declared config/input/collateral closure";
  let metrics = get raw "metrics" in
  List.iter (fun (_, metric) ->
    match get metric "availability" with
    | J.String "unavailable" -> ()
    | J.String "available" -> (match get metric "value" with
        | J.Integer _ | J.Float _ -> () | _ -> fail "available metric must be finite numeric evidence")
    | _ -> fail "metric must declare availability") (fields metrics);
  { schema; identity; location; boundary; restart_targets; files; external_files; metrics;
    compatibility = get raw "compatibility"; raw }
let of_json j = protect (fun () -> decode j)
let parse s = protect (fun () -> decode (codec (J.parse s)))
let json_file p = codec (Result_store.read_json p)
let read p = protect (fun () -> decode (json_file (Filename.concat p "manifest.json")))
let to_json t = t.raw
let read_bytes p = let ic = open_in_bin p in
  Fun.protect ~finally:(fun () -> close_in_noerr ic) (fun () -> really_input_string ic (in_channel_length ic))
let measure_file p =
  if (Unix.stat p).Unix.st_kind <> Unix.S_REG then fail ("missing artifact: " ^ p);
  let ic = open_in_bin p in
  let sha256 = Fun.protect ~finally:(fun () -> close_in_noerr ic) (fun () ->
    Cryptokit.hash_channel (Cryptokit.Hash.sha256 ()) ic |> Cryptokit.transform_string (Cryptokit.Hexa.encode ())) in
  { sha256; bytes = Int64.to_string (Unix.LargeFile.stat p).Unix.LargeFile.st_size }
let file_record p = protect (fun () -> measure_file p)
let starts s prefix = String.starts_with ~prefix s
(* Native state is never canonicalized, written, or used as identity JSON.
   This private read-only syntax projection masks native nonfinite numeric tokens
   with a numeric sentinel; only its view leaves and metrics container are used.
   Nonfinite view leaves still fail the path-only check. Quoted strings are copied
   verbatim, so paths/keys containing Infinity are never rewritten. *)
let state_projection bytes =
  let b = Buffer.create (String.length bytes) in
  let n = String.length bytes in
  let rec loop i quoted escaped =
    if i < n then let c = bytes.[i] in
      if quoted then (Buffer.add_char b c;
        loop (i+1) (if escaped then true else c <> '"') (not escaped && c = '\\'))
      else if c = '"' then (Buffer.add_char b c; loop (i+1) true false)
      else if List.mem c [' '; '\n'; '\r'; '\t'; '{'; '}'; '['; ']'; ':'; ','] then
        (Buffer.add_char b c; loop (i+1) false false)
      else (
        let j = ref i in
        while !j < n && not (List.mem bytes.[!j] [' '; '\n'; '\r'; '\t'; '{'; '}'; '['; ']'; ':'; ','; '"']) do incr j done;
        let token = String.sub bytes i (!j-i) in
        let replacement = if List.mem token ["Infinity"; "-Infinity"; "NaN"] then "0"
          else match J.parse token with Error J.Nonfinite -> "0" | _ -> token in
        Buffer.add_string b replacement; loop !j false false)
  in loop 0 false false; codec (J.parse (Buffer.contents b))
let validate ?(restart=false) ?restart_to ?(check_external=true) ?location ~compatibility path = protect (fun () ->
  let path = normalize_path path in
  let location = match location with None -> path | Some p -> normalize_path p in
  let t = decode (json_file (Filename.concat path "manifest.json")) in
  if t.location <> location then fail "checkpoint identity/location mismatch";
  if not (compatibility_equal t.compatibility compatibility) then fail "incompatible target, tools, runtime, bridge or workspace";
  if restart && t.restart_targets = [] then fail "checkpoint has no tested restart target";
  (match restart_to with Some target when not (List.mem target t.restart_targets) ->
     fail ("untested restart transition: " ^ Stage.to_string t.boundary ^ " -> " ^ Stage.to_string target) | _ -> ());
   if t.schema <> Schema_1 then (
    let image = try get (get (get compatibility "binding") "image_identity") "image_id" with Invalid _ -> J.null in
    if image <> str (Stage.capability t.boundary).tested_image_id then
      fail "restart eligibility is tested only in the declared exact image");
  let rec walk relative =
    let directory = if relative = "" then path else Filename.concat path relative in
    Array.to_list (Sys.readdir directory) |> List.concat_map (fun name ->
      let name = if relative = "" then name else relative ^ "/" ^ name in
      match (Unix.lstat (Filename.concat path name)).Unix.st_kind with
      | Unix.S_REG -> [name]
      | Unix.S_DIR -> walk name
      | _ -> fail ("missing/corrupt checkpoint artifact: " ^ name)) in
  let sorted = List.sort String.compare in
  if sorted (walk "") <> sorted ("manifest.json" :: List.map fst t.files) then fail "checkpoint file inventory mismatch";
   List.iter (fun (name, expected) -> if measure_file (Filename.concat path name) <> expected then
     fail ("missing/corrupt checkpoint artifact: " ^ name)) t.files;
   if t.schema=Schema_3 then (
     let report=json_file (Filename.concat path "segment.json") in
     if get report "macro_profile" <> get t.raw "macro_profile" then fail "macro checkpoint/report profile mismatch";
     let entries j=match macro_evidence ~boundary:t.boundary j with Ok xs -> xs | Error e -> fail e in
     let without_path e=codec (J.unsigned e ~field:"path") in
     if List.map without_path (entries (get report "macro_evidence")) <>
        List.map without_path (entries (get t.raw "macro_evidence")) then fail "macro checkpoint/report evidence mismatch");
  let state = state_projection (read_bytes (Filename.concat path "state.json")) in
  ignore (fields (get state "metrics"));
  let views = List.filter (fun (k, _) -> k <> "metrics") (fields state) in
  let truthy = function J.Null | J.Bool false | J.String "" | J.Array [] | J.Object [] | J.Integer "0" -> false | _ -> true in
  let required = if t.boundary = Stage.Synthesis then ["nl"; "json_h"] else ["odb"; "def"] in
  List.iter (fun key -> match List.assoc_opt key views with Some v when truthy v -> ()
    | _ -> fail "missing required boundary view") required;
  let rec paths = function
    | J.Null -> ()
    | J.Object xs -> List.iter (fun (_, v) -> paths v) xs
    | J.Array xs -> List.iter paths xs
    | J.String p ->
      if not (absolute p) then fail "state view escapes checkpoint";
      let p = normalize_path p in
      if not (absolute p) || not (starts p (location ^ "/views/")) then fail "state view escapes checkpoint";
      let relative = String.sub p (String.length location + 1) (String.length p - String.length location - 1) in
      if not (List.mem_assoc relative t.files) then fail "state view not inventoried"
    | _ -> fail "invalid state view leaf" in
  List.iter (fun (_, v) -> paths v) views;
  if not (equal (json_file (Filename.concat path "closure.json")) (get t.raw "external")) then
    fail "missing declared config/input/collateral closure";
  if check_external then List.iter (fun (name, expected) ->
    if not (starts name "/nix/store/") && measure_file name <> expected then fail ("changed external dependency: " ^ name)) t.external_files;
  t)
let inspection ~compatibility_source t = obj
  ["validation", str "host_only"; "runtime_closure_validated", J.bool false;
   "compatibility_source", str (match compatibility_source with `Expected -> "expected" | `Recorded -> "recorded");
   "manifest", t.raw]
