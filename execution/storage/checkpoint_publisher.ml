module J = Identity_json
module Stage = Hardcaml_asic.Flow.Stage
type runtime_outcome = Validated | Failed of string | Interrupted of int
type runtime_request = { staging : string; location : string; boundary : Stage.t; compatibility : J.t }
type publication = { path : string; checkpoint : Checkpoint.t; runtime_validation : [ `Not_required | `Validated ] }
type error = Publication_failed of string | Runtime_failed of string | Runtime_interrupted of int | Cleanup_failed of error * string
exception Cleanup_error of exn * string
exception Callback_raised of exn * Printexc.raw_backtrace
let rec error_message = function Publication_failed s | Runtime_failed s -> s
  | Runtime_interrupted n -> "runtime validation interrupted: " ^ string_of_int n
  | Cleanup_failed (e,s) -> error_message e ^ "; owned staging cleanup failed: " ^ s
exception Failure of error
let fail s = raise (Failure (Publication_failed s))
let checked = function Ok x -> x | Error s -> fail s
let codec = function Ok x -> x | Error e -> fail (J.error_message e)
let str s = codec (J.string s)
let obj xs = codec (J.object_ xs)
let get j k = match j with J.Object xs -> (match List.assoc_opt k xs with Some v -> v | None -> fail ("missing " ^ k)) | _ -> fail "expected object"
let to_json p =
  let t = p.checkpoint in
  let capability = Stage.capability t.boundary in
  obj ["path", str p.path; "identity", str t.identity; "boundary", str (Stage.to_string t.boundary);
    "restart_supported", J.bool (t.restart_targets <> []);
    "restart_targets", J.array (List.map (fun s -> str (Stage.to_string s)) t.restart_targets);
    "closure_scope", str "path_bound"; "tested_image_id", str capability.tested_image_id;
    "acceptance", str "not_evaluated"]
let read p = let ic = open_in_bin p in Fun.protect ~finally:(fun () -> close_in_noerr ic)
  (fun () -> really_input_string ic (in_channel_length ic))
let write p bytes = let fd = Unix.openfile p [Unix.O_WRONLY; Unix.O_CREAT; Unix.O_EXCL] 0o600 in
  let oc = Unix.out_channel_of_descr fd in
  Fun.protect ~finally:(fun () -> close_out_noerr oc) (fun () -> output_string oc bytes; close_out oc)
let record p = checked (Checkpoint.file_record p)
let stable_stat p = let st = Unix.LargeFile.stat p in { st with Unix.LargeFile.st_atime = 0. }
let snapshot p =
  let before = stable_stat p in
  let measured = record p in
  let after = stable_stat p in
  if before <> after then fail ("source changed while hashing: " ^ p);
  after, measured
let unchanged p expected = if snapshot p <> expected then fail ("source changed during publication: " ^ p)
let copy source dest expected =
  unchanged source expected;
  let ic = open_in_bin source in
  Fun.protect ~finally:(fun () -> close_in_noerr ic) (fun () ->
    let fd = Unix.openfile dest [Unix.O_WRONLY; Unix.O_CREAT; Unix.O_EXCL] 0o600 in
    let oc = Unix.out_channel_of_descr fd in
    Fun.protect ~finally:(fun () -> close_out_noerr oc) (fun () ->
      let buf = Bytes.create 65536 in
      let rec loop () = let n = input ic buf 0 (Bytes.length buf) in
        if n > 0 then (output oc buf 0 n; loop ()) in loop (); close_out oc));
  unchanged source expected;
  if record dest <> snd expected then fail ("copy mismatch: " ^ source)
let random_name () =
  let ic = open_in_bin "/dev/urandom" in
  let bytes = Fun.protect ~finally:(fun () -> close_in_noerr ic) (fun () -> really_input_string ic 16) in
  Cryptokit.transform_string (Cryptokit.Hexa.encode ()) bytes
external promote : string -> string -> unit = "hardcaml_asic_rename_noreplace"
let rec cleanup p =
  match (Unix.lstat p).Unix.st_kind with
  | Unix.S_DIR -> Unix.chmod p 0o700; Array.iter (fun n -> cleanup (Filename.concat p n)) (Sys.readdir p); Unix.rmdir p
  | _ -> Unix.unlink p
let rec readonly p = match (Unix.lstat p).Unix.st_kind with
  | Unix.S_DIR -> Array.iter (fun n -> readonly (Filename.concat p n)) (Sys.readdir p); Unix.chmod p 0o555
  | Unix.S_REG -> Unix.chmod p 0o444
  | _ -> fail "nonregular staging artifact"
let publish ?(name=random_name) ?validate_runtime ?before_promote ~candidate ~store ~compatibility ~boundary () =
  let owned = ref None in
  let run () =
    let candidate = checked (Checkpoint.absolute_path candidate) and store = checked (Checkpoint.absolute_path store) in
    if (Unix.stat store).Unix.st_kind <> Unix.S_DIR then fail "checkpoint store must already exist";
    let manifested = try ignore (Unix.lstat (Filename.concat candidate "manifest.json")); true
      with Unix.Unix_error (Unix.ENOENT,_,_) -> false in
    if manifested then fail "candidate must be unpublished backend output, not a checkpoint/restart input";
    let metadata = List.map (fun n -> let p = Filename.concat candidate n in n, (p, snapshot p))
      ["state.json"; "resolved.json"; "closure.json"; "segment.json"] in
    let source n = fst (List.assoc n metadata) in
     let report = codec (J.parse (read (source "segment.json"))) in
     let macro = match report with J.Object xs -> List.mem_assoc "macro_profile" xs | _ -> false in
     let macro_entries = if not macro then [] else (
       if get report "macro_profile" <> str Macro_contract.segmented_profile then fail "unsupported macro publication profile";
       checked (Checkpoint.macro_evidence ~boundary (get report "macro_evidence"))) in
    if get report "status" <> str "completed" || get report "boundary" <> str (Stage.to_string boundary)
       || get report "bridge_sha256" <> get compatibility "bridge_sha256" then fail "inconsistent outer segment completion";
    let state = checked (Backend_state.parse (read (source "state.json"))) in
    let suffix = name () in
    if suffix = "" || suffix = "." || suffix = ".." || String.contains suffix '/' || String.contains suffix '\000' then fail "invalid publication name";
    let staging = Filename.concat store (".publishing-" ^ suffix) in
    let location = Filename.concat store (Stage.to_string boundary ^ "-" ^ suffix) in
    Unix.mkdir staging 0o700; owned := Some staging;
    Unix.mkdir (Filename.concat staging "views") 0o700;
    let count = ref 0 and views = ref [] and files = ref [] in
     let state = Backend_state.map_paths state ~f:(fun source ->
      let source = checked (Checkpoint.absolute_path source) in
      let expected = snapshot source in
      incr count;
      let relative = Printf.sprintf "views/%05d-%s" !count (Filename.basename source) in
      let dest = Filename.concat staging relative in
      copy source dest expected;
      views := (source, expected) :: !views;
      files := (relative, record dest) :: !files;
       Filename.concat location relative) in
     let macro_entries=List.mapi (fun index entry ->
       let source=match get entry "path" with J.String p -> checked (Checkpoint.absolute_path p) | _ -> fail "macro evidence path required" in
       let expected=snapshot source in
       let actual=snd expected in
       if get entry "sha256" <> str actual.sha256 || get entry "bytes" <> codec (J.integer actual.bytes) then
         fail "macro evidence changed before publication";
       let relative=Printf.sprintf "views/macro-evidence-%05d.json" index in
       copy source (Filename.concat staging relative) expected;
       views := (source,expected)::!views;
       files := (relative,actual)::!files;
       match entry with J.Object xs -> obj (("path",str (Filename.concat location relative))::List.remove_assoc "path" xs)
       | _ -> assert false) macro_entries in
    write (Filename.concat staging "state.json") (Backend_state.serialize state);
    List.iter (fun n -> let p, expected = List.assoc n metadata in copy p (Filename.concat staging n) expected)
      ["resolved.json"; "closure.json"; "segment.json"];
    List.iter (fun n -> files := (n, record (Filename.concat staging n)) :: !files)
      ["state.json"; "resolved.json"; "closure.json"; "segment.json"];
    let external_files = codec (J.parse (read (Filename.concat staging "closure.json"))) in
    let capability = Stage.capability boundary in
     let restart_targets=if not macro then capability.restart_targets else
       match boundary with Stage.Synthesis -> [Stage.Floorplan] | Stage.Timing -> [] | _ -> capability.restart_targets in
     let unsigned = obj (["schema_version", codec (J.integer (if macro then "3" else "2")); "location", str location;
       "boundary", str (Stage.to_string boundary); "restart_supported", J.bool (restart_targets <> []);
       "restart_targets", J.array (List.map (fun s -> str (Stage.to_string s)) restart_targets);
      "tested_image_id", str capability.tested_image_id; "closure_scope", str "path_bound";
      "compatibility", compatibility; "files", obj (List.map (fun (n,(r:Checkpoint.file_record)) ->
        n, obj ["sha256", str r.sha256; "bytes", codec (J.integer r.bytes)]) !files);
       "external", external_files; "metrics", get report "metrics"; "acceptance", str "not_evaluated"] @
       if macro then ["macro_profile",str Macro_contract.segmented_profile;"macro_evidence",J.array macro_entries] else []) in
    let manifest = match unsigned with J.Object xs -> obj (("identity", str (J.identity unsigned)) :: xs) | _ -> assert false in
    write (Filename.concat staging "manifest.json") (J.canonical manifest ^ "\n");
    let validate () =
      let t = checked (Checkpoint.validate ~location ~compatibility staging) in
      if J.canonical (Checkpoint.to_json t) <> J.canonical manifest then fail "validator mutated manifest";
      let rec directories relative =
        let p = if relative = "" then staging else Filename.concat staging relative in
        Array.to_list (Sys.readdir p) |> List.concat_map (fun n ->
          let child = if relative = "" then n else relative ^ "/" ^ n in
          if (Unix.lstat (Filename.concat staging child)).Unix.st_kind = Unix.S_DIR
          then child :: directories child else []) in
      if directories "" <> ["views"] then fail "validator mutated staging directories";
      List.iter (fun (_, (p, expected)) -> unchanged p expected) metadata;
      List.iter (fun (p, expected) -> unchanged p expected) !views;
      t in
    ignore (validate ());
    let image_only = match external_files with J.Object xs -> List.exists (fun (n,_) -> String.starts_with ~prefix:"/nix/store/" n) xs | _ -> false in
     let runtime_validation = if not (image_only || macro) then `Not_required else (
      match validate_runtime with
      | None -> fail "image-only closure requires explicit runtime validator"
      | Some callback -> match (try callback { staging; location; boundary; compatibility }
          with exn -> raise (Callback_raised (exn, Printexc.get_raw_backtrace ()))) with
        | Validated -> `Validated
        | Failed s -> raise (Failure (Runtime_failed s))
        | Interrupted n -> raise (Failure (Runtime_interrupted n))) in
    ignore (validate ());
    readonly staging;
    let checkpoint = validate () in
    Option.iter (fun callback -> try callback staging with exn ->
      raise (Callback_raised (exn, Printexc.get_raw_backtrace ()))) before_promote;
    promote staging location;
    owned := None;
    { path = location; checkpoint; runtime_validation } in
  let outcome = try `Result (try Ok (run ()) with
    | Failure e -> Error e
    | Sys_error s -> Error (Publication_failed s)
    | Unix.Unix_error (e,op,p) -> Error (Publication_failed (op ^ ": " ^ p ^ ": " ^ Unix.error_message e)))
    with Callback_raised (exn,bt) -> `Exception (exn,bt)
       | exn -> `Exception (exn, Printexc.get_raw_backtrace ()) in
  let cleanup_error = match !owned with None -> None | Some p ->
    (try cleanup p; None with exn -> Some (Printexc.to_string exn)) in
  match outcome, cleanup_error with
  | `Result r, None -> r
  | `Result (Error e), Some s -> Error (Cleanup_failed (e,s))
  | `Result (Ok _), Some _ -> assert false
  | `Exception (exn,bt), None -> Printexc.raise_with_backtrace exn bt
  | `Exception (exn,_), Some s -> raise (Cleanup_error (exn,s))
