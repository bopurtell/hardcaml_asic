(* University of Florida *)
(* Author: Bohdan Purtell *)
(* Module: "elaboration/bundle.mli" *)
(* A deterministic Tiny Tapeout / LibreLane project bundle.

   A bundle is every file the flow needs for one design, plus a manifest recording where
   each byte came from. It is built in memory by {!render} and put on disk by {!write}:

   {v
   Resolved.t (+ optional Simulation Elaborated_project.t)
     -> render     RTL, config, SDC and info.yaml generated; source inputs read and
                   hashed; manifest and identity computed; nothing written
     -> write      files created under a new or empty directory
   v}

   Guarantees:
   1. the same resolved flow, simulation project, inputs and git state render to the
      same bytes and the same {!identity};
   2. the identity changes when any emitted file, declared input, input git status or
      source revision changes;
   3. every declared input's exact bytes are copied under [inputs/], including dirty and
      untracked files, so the source that produced a bundle is retrievable from it;
    4. synthesis and simulation RTL sit in separate directories and are listed separately
       in the manifest; they are never meant to be compiled together. Exact SRAM
       synthesis adds a generated empty stub through EXTRA_VERILOG_MODELS. The
       macro_contract separately inventories vendor functional and timed models;
        do not use the empty synthesis stub for implementation simulation.
    5. exact SRAM Hardening adds schema-2 [fresh_full] macro metadata and the
       identity-covered [physical/macro-plan.json] and [physical/pdn.tcl]. The
       generated runtime checks loaded geometry; emission is not physical proof.

   It does NOT validate the design or target; {!Resolved.resolve} already did. It
   does NOT invoke any EDA tool, access the PDK, or link the TT support-tools checkout
   expected at [OUTPUT/tt]; running the flow is execution work. Layout and manifest fields are
   described in docs/guides/bundle-emission.md.
*)

open! Core

(** Abstract; the only way to get one is {!render}, so the files, manifest and identity
    always agree. *)
type t

(** [render ~simulation_project ~source_root:"." ~input_paths:[ "lib/dune" ] resolved]

    [input_paths] are source files relative to [source_root], which must be inside a git
    checkout with at least one commit. Their order and repeats do not matter. Without
    [simulation_project], simulation consumers are pointed at the synthesis RTL.

    Returns an error, and writes nothing, if [simulation_project] is not a Simulation
    elaboration of the same project and top module, if [input_paths] is empty, if any input
    path is absolute or contains an empty, ["."] or [".."] component, if any input is missing, a
    directory, or resolves outside [source_root], or if git fails. *)
val render
   :  ?simulation_project:Elaborated_project.t
   -> ?timing_physical_qualification:bool
  -> source_root:string
  -> input_paths:string list
  -> Resolved.t
   -> t Or_error.t

(** [timing_physical_qualification=true] explicitly emits physical resolution v2
    and requests all-configured-corner synthesis/post-PNR qualification. Requires
    macro-free timing intent. Native resolution requires the complete sequential
    inventory to equal the declared stages. Default rendering remains v1; this
    flag is a qualification request, not evidence of native acceptance. *)

(** Write a bundle under [output_dir], creating it and its parents when missing. Does not
    invoke any EDA tool or access the PDK.

    Returns an error if [output_dir] exists and is not a directory, or is not empty; so an
    existing file is never replaced. Writing is not atomic: a failure partway leaves a
    partial, non-empty directory that must be deleted before writing there again. *)
val write : t -> output_dir:string -> unit Or_error.t

(** Result of identity-addressed publication. *)
module Publication : sig
  type t = private
    { directory : string (** Absolute public identity path, retaining its root link. *)
    ; reused : bool (** True only after exact verification of an existing bundle. *)
    }
end

(** [publish t ~store_dir] publishes at [store_dir/<identity>]. The explicit store
    may be relative. Missing real directory parents are created; symlinked store
    ancestors and non-directory parents fail. No existing destination is overwritten,
    including an empty directory. Reuse requires exact paths and bytes (manifest
    included), with no extra files, empty directories, or symlinked members.

    On local POSIX filesystems, a unique private [store_dir/.objects/<identity>.*]
    directory is written and verified before an exclusive relative symlink publishes
    it. Existing links must name a direct identity-prefixed object in that layout;
    exact existing plain directories are also reusable. Competing publishers yield
    one creation and verified reuse. Only this call's unpublished object is cleaned
    up; published objects must be preserved. Store contents must not be mutated by
    other actors while publishing or verifying. No GC, fsync/power-loss durability
    or NFS guarantees are provided. No EDA or runtime Python is required. *)
val publish : t -> store_dir:string -> Publication.t Or_error.t

(** [file t "src/config.json"]; the contents at a bundle relative path, if emitted. *)
val file : t -> string -> string option

(** Every bundle relative path, sorted, except ["manifest.json"], which is always last. *)
val paths : t -> string list

(** The manifest, including its [identity] field; the same value [manifest.json] holds. *)
val manifest : t -> Yojson.Safe.t

(** Lowercase hex SHA-256 of the manifest without its [identity] field. *)
val identity : t -> string

(** Pure verification using the existing order-sensitive bundle identity algorithm.
    When [resource_snapshot] is present, strictly checks its resolved implementation
    schema and association with project, top, resource and macro inventories.
     Present [hardware_variant] is strictly decoded including its inner identity;
     null is invalid. Historical absence remains supported without rewriting the manifest.
    This verifies the manifest assertion, not files on disk or a source stamp. *)
val verify_manifest_identity : Yojson.Safe.t -> unit Or_error.t
