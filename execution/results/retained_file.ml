external open_readonly : string -> Unix.file_descr = "hardcaml_asic_retained_open"
let exists path = try ignore (Unix.lstat path);true
  with Unix.Unix_error (Unix.ENOENT,_,_) -> false
let read ?(max_bytes=32*1024*1024) path =
  if max_bytes<0 || String.contains path '\000' then invalid_arg "invalid retained metadata admission";
  let fd=open_readonly path in
  let ic=try Unix.in_channel_of_descr fd with exn ->
    let backtrace=Printexc.get_raw_backtrace () in
    (* Until conversion succeeds the raw descriptor is ours. Afterwards only
       channel cleanup below owns it; closing both would risk descriptor reuse. *)
    (try Unix.close fd with Unix.Unix_error _ -> ());
    Printexc.raise_with_backtrace exn backtrace in
  Fun.protect ~finally:(fun () -> close_in_noerr ic) (fun () ->
    let before=Unix.fstat fd in
    if before.st_kind<>Unix.S_REG then invalid_arg ("retained source must be a regular file: "^path);
    if before.st_size>max_bytes then invalid_arg ("retained source byte bound exceeded: "^path);
    let raw=really_input_string ic before.st_size in
    let after=Unix.fstat fd and named=Unix.lstat path in
    let same a b = a.Unix.st_dev=b.Unix.st_dev && a.st_ino=b.st_ino && a.st_kind=b.st_kind
      && a.st_size=b.st_size && a.st_mtime=b.st_mtime && a.st_ctime=b.st_ctime in
    if not (same before after && same after named) then invalid_arg ("retained source changed during read: "^path);
    raw)
