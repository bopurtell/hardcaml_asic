(* University of Florida *)
(* Author: Bohdan Purtell *)
(* Module: "single_port_ram.ml" *)
(* Source implementation of the single-port 1RW program memory. A single-port RAM is the
   design's answer to "store a word here, and give it back one cycle after I ask";

   A design constructor calls [create] with the Elaboration.Context that
   Project.elaborate handed it. [create] checks every port width, then registers
   [request] through Elaboration.Context.register_exn, which runs Resource.Policy.lookup
   and Selection.select and stores the Resource.Record. The elaboration mode plus the
   selected implementation then decide which hardware is built: [behavioral] in
    Simulation, [flops] or the selected supported macro in Implementation;

   docs/reference/program-memory-contract.md is normative, and both implementations here are written
   against it rather than against each other. Anything the contract leaves unspecified
   (output after a write, unwritten reads, out-of-range access) is NOT something the two
   are meant to agree on; see contract section 9 for how that is verified.

   It does NOT own program validity, loading or fetch bounds checking; those belong to the
   consumer (contract section 10). Implementation can also instantiate the stock
   exact 256x16 CMOS5L macro, with no extra registers. Unknown macro mappings fail.
   Which sources the resource carries is Elaboration.Context.sources_of's job,
   not this module's.
*)

open! Core
open! Hardcaml

[@@@ocamlformat "disable"]

(* The compile-time shape of one RAM instance;

   width        : bits per word; also the width of [write_data] and the read data;
   depth        : number of words; need not be a power of two, see [address_valid];
   read_latency : cycles from an enabled read to its data; only 1 is accepted in v0.1;

   The type is private in the mli, so the only way to build one is through [create_exn],
   which means every config in hand has already been validated. A shape that is rejected
   is never rounded to a nearby one.
*)
module Config = struct
  type t =
    { width        : int
    ; depth        : int
    ; read_latency : int
    }
  [@@deriving sexp_of]

  (* Width of the [address] port, [Int.ceil_log2 depth]; *)
  let address_bits t = Int.ceil_log2 t.depth

  (* Total stored bits, [width * depth]; meant for area comparisons between candidate
     shapes (contract section 11), not for a design to branch on; *)
  let storage_bits t = t.width * t.depth

  (* Build a config; raises rather than returning Or_error since configs are fixed values
     declared at module level or inside a design constructor (see
     test/test_single_port_ram.ml and examples/memory_example.ml), where there is nothing
     to thread an error through;

     width < 1         -> error; a zero-width word stores nothing;
     depth < 2         -> error; depth 1 would give a zero-width address port, which is
                          not a memory;
     read_latency <> 1 -> error; latency is a parameter on purpose, so the call site
                          states it and a future latency-2 macro has somewhere to live,
                          but v0.1 only implements 1 and refuses to round anything to it;

     The checks run in the order above and the first failure raises, so a config that is
     wrong in two ways only reports the first.
  *)
  let create_exn ~width ~depth ~read_latency =

    if width < 1 then raise_s [%message "[width] must be at least 1" (width : int)];
    if depth < 2 then raise_s [%message "[depth] must be at least 2" (depth : int)];
    if read_latency <> 1
    then
      raise_s
        [%message
          "v0.1 implements [read_latency = 1] only; see docs/reference/program-memory-contract.md"
            (read_latency : int)];
    { width; depth; read_latency }
  ;;
end

(* The resource request registered for one RAM; its contract is the whole promise the
   primitive makes, so a technology mapping has to match all of it;

   port            : "1rw", one shared address for reads and writes;
   disabled_output : "hold", the read data keeps its value while [enable] is low;
   write_output    : "unspecified", the read data after a write promises nothing;

   Careful: it inherits the field order pitfall described on Technology.exact_mapping. A
   mapping written as [((depth 3) (width 8) ...)] never matches this request. Under an
   Exact policy that fails selection loudly; under Exact_or_flop_fallback it builds flops,
   recorded as Flop_fallback in the Selection.Reason but easy to miss. Use the
   canonical Resource.Request.single_port_ram builder rather than writing the
   contract by hand.
*)
let request (config : Config.t) =
  Resource.Request.single_port_ram
    ~width:config.width ~depth:config.depth ~read_latency:config.read_latency
;;

(* The behavioral model's diagnostic value for word [word]; alternating bits, with the
   least significant bit set for even words;

   width 8, word 0 -> 0x55;
   width 8, word 1 -> 0xaa;

   Why alternating bits: the pattern is implausible as real data and obvious in a
   waveform, and it changes with word parity so a stuck output is visible too (contract
   section 5.1). It is still an ordinary two-state value; nothing fails just because a
   consumer reads it.
*)
let poison ~width ~word =
  Bits.of_string
    (String.init width ~f:(fun bit ->
       if (width - bit - 1 + word) mod 2 = 0 then '1' else '0'))
;;

(* High when [address] names a real word, i.e. [address < depth];

   A power-of-two depth makes every address encodable in [address_bits] valid, so the
   comparison is skipped and the result is a constant vdd. Otherwise the top addresses
   are out of contract (contract section 6) and this is where they get caught.

   Why the [Sys.int_size] guard: it keeps [1 lsl bits] from overflowing. A positive int
   depth never needs that many address bits, so the guard is defensive only;
*)
let address_valid (config : Config.t) address =

  let bits = Config.address_bits config in
  if bits < Stdlib.Sys.int_size - 1 && config.depth = 1 lsl bits
  then Signal.vdd
  else Signal.(address <: of_int_trunc ~width:bits config.depth)
;;

(* The behavioral simulation model; a Hardcaml memory initialized to poison, read
   combinationally and registered, so reads land one cycle later;

   enable, write_enable, valid address     -> write [write_data]; output write poison;
   enable, NOT write_enable, valid address -> output the addressed word;
   enable, invalid address                 -> no write; output invalid-address poison;
   NOT enable                              -> no write; output holds;

   Memory starts as poison and NOT zeros on purpose: zero-fill is the failure mode that
   passes in simulation and fails in silicon, since firmware assuming a cleared memory
   meets an uninitialized macro (contract section 5.1). An unwritten read therefore
   returns the poison for that word's parity.

   Write poison is always the word 0 pattern and invalid-address poison is always the
   word 1 pattern, whatever the address; -> see the scoreboard in
   test/test_single_port_ram.ml, which checks both.

   Careful: the poison belongs to this model only. It must never leak into [flops] or a
   macro, which would turn a diagnostic into an initialization guarantee (contract section
   5.1); "flop RTL renders without behavioral initialization" in
   test/test_single_port_ram.ml checks that [flops] renders no "initial" block.
*)
let behavioral
    (config : Config.t)
    ~name (* instance name; the memory is named [name ^ "_behavioral"] *)
    ~clock
    ~enable
    ~write_enable
    ~address
    ~write_data
  : Signal.t
  =
  let valid = address_valid config address in
  let write = Signal.(enable &: write_enable &: valid) in

  (* The one write port; gated by [valid] so an out-of-range write can never wrap onto a
     real word *)
  let write_port : Signal.t Write_port.t =
    { write_clock   = clock
    ; write_address = address
    ; write_enable  = write
    ; write_data
    }
  in

  let initial =
    Array.init config.depth ~f:(fun word -> poison ~width:config.width ~word)
  in

  (* The combinational read of [address]; registered below, which gives latency 1 *)
  let data =
    Signal.multiport_memory
      ~name:(name ^ "_behavioral")
      ~initialize_to:initial
      config.depth
      ~write_ports:[| write_port |]
      ~read_addresses:[| address |]
    |> fun outputs -> outputs.(0)
  in

  let write_poison = Signal.of_bits (poison ~width:config.width ~word:0) in
  let invalid_address_poison = Signal.of_bits (poison ~width:config.width ~word:1) in

  (* What the output register loads when enabled; [valid] is checked first, so the raw
     memory read is never used for an out-of-range address *)
  let next =
    Signal.mux2
      valid
      (Signal.mux2 write_enable write_poison data)
      invalid_address_poison
  in

  (* Registering on [enable] is what makes a disabled cycle hold (contract section 4.1) *)
  Signal.reg ~enable (Signal.Reg_spec.create ~clock ()) next
;;

(* The synthesizable flop implementation; one register per word and a registered read mux;

   enable, write_enable      -> write the addressed word; output holds;
   enable, NOT write_enable  -> output the addressed word;
   NOT enable                -> nothing changes; output holds;

   There is no reset and no initialization on purpose; contents are undefined at power-up
   by contract (section 5), and the consumer tracks validity.

   Out-of-range addresses are out of contract and get no diagnostic here. A write matches
   no word, so nothing is written. A read goes through Signal.mux, which repeats its last
   input for indices past the end of the list, so it returns the top word; that is fine
   since the contract promises nothing for it.
*)
let flops
    (config : Config.t)
    ~clock
    ~enable
    ~write_enable
    ~address
    ~write_data
  : Signal.t
  =
  let spec = Signal.Reg_spec.create ~clock () in

  (* The storage; word [index] loads [write_data] only on an enabled write to [index] *)
  let words =
    List.init config.depth ~f:(fun index ->
      let selected =
        Signal.(address ==: of_int_trunc ~width:(Config.address_bits config) index)
      in
      let write = Signal.(enable &: write_enable &: selected) in
      Signal.reg ~enable:write spec write_data)
  in

  let data = Signal.mux address words in

  (* A write's output is unspecified; holding the previous output costs no extra hardware
     and obeys the contract. An enabled read registers the selected word. *)
  let read = Signal.(enable &: ~:write_enable) in
  Signal.reg ~enable:read spec data
;;

(* The main sauce here for the RAM; check the ports, register the instance, and build
   whichever implementation was selected;

   1. build the instance identity from the context's scope and [name], only so errors
      can name it; an invalid [name] raises here;
   2. check every port width; the first mismatch raises with the instance and port;
   3. register [request] through Elaboration.Context.register_exn, which raises on a
      duplicate name, a missing policy, or a requirement the technology cannot meet;
   4. build from the mode and the selected implementation, per the table below;

   Mode             Implementation   Result
   Simulation     , any           -> behavioral;
   Implementation , Flops         -> flops;
   Implementation , supported Macro -> direct external macro;
   Implementation , unknown Macro   -> error;

   The width checks run before registration, so a RAM with a mis-sized port never enters
   the inventory. Raises rather than returning Or_error since it runs inside
   a design constructor, which would otherwise have to thread Or_error through every
   hierarchy level; Project.elaborate catches the raise and reports it as a failed
   construction.

   Simulation ignores the selection on purpose: every resource simulates with its
   behavioral model, even when a macro was selected, so the macro case is NOT an error
   there. The record still says what was selected.

   The match has no top-level wildcard, so a new Elaboration.Mode or Implementation
   constructor fails to compile here until someone decides what it builds -> see
   test/test_single_port_ram.ml;
*)
let create
    context
    ?(name = "ram") (* distinct names are needed for several RAMs in one scope *)
    config
    ~clock
    ~enable
    ~write_enable
    ~address
    ~write_data
  : Signal.t
  =
  (* TODO (future fix): this rebuilds the identity the same way
     Elaboration.Context.register_exn does, so the two can drift apart. The identity is
     needed before registration for the width errors; exposing the identity derivation
     from Elaboration.Context would remove the copy; *)
  let path =
    Scope.path (Elaboration.Context.scope context) |> Scope.Path.to_list |> List.rev
  in

  let instance = Resource.Id.create_exn ~path ~name in

  (* Raise if [signal] is not [expected] bits wide *)
  (* TODO (future fix): the message does not follow the structured diagnostic convention,
     which wants "what went wrong; how to fix it". Something like "single-port RAM port
     width mismatch; size the port to match the config". Changing it changes expect test
     output, so update those in the same commit; *)
  let check port signal expected =
    let actual = Signal.width signal in
    if actual <> expected
    then
      raise_s
        [%message
          "single-port RAM port width mismatch"
            (instance : Resource.Id.t)
            (port : string)
            (expected : int)
            (actual : int)]
  in

  check "clock" clock 1;
  check "enable" enable 1;
  check "write_enable" write_enable 1;
  check "address" address (Config.address_bits config);
  check "write_data" write_data config.width;

  (* Register and select; raises on a duplicate name or an unsatisfiable policy *)
  let record = Elaboration.Context.register_exn context ~name (request config) in
  match Elaboration.Context.mode context, record.selection.implementation with

  (* Simulation; always the behavioral model, whatever was selected *)
  | Simulation, _ ->
    behavioral config ~name ~clock ~enable ~write_enable ~address ~write_data

  (* Flops selected, explicitly or as a recorded fallback *)
  | Implementation, Flops ->
    flops config ~clock ~enable ~write_enable ~address ~write_data

  (* The macro's output is already synchronous. Only combinational controls are
     adapted here: adding a register would silently change the portable latency. *)
  | Implementation, Macro "RM_IHPSG13_1P_256x16_c2_bm_bist"
    when config.width = 16 && config.depth = 256 && config.read_latency = 1 ->
    let open Signal in
    let inst =
      Instantiation.create ()
        ~name:"RM_IHPSG13_1P_256x16_c2_bm_bist"
        ~instance:(Resource.Id.rtl_instance_name record.id)
        ~inputs:
          [ "A_CLK", clock
          ; "A_MEN", enable
          ; "A_WEN", enable &: write_enable
          ; "A_REN", enable &: ~:write_enable
          ; "A_ADDR", address
          ; "A_DIN", write_data
          ; "A_DLY", vdd
          ; "A_BM", ones 16
          ; "A_BIST_CLK", gnd
          ; "A_BIST_EN", gnd
          ; "A_BIST_MEN", gnd
          ; "A_BIST_WEN", gnd
          ; "A_BIST_REN", gnd
          ; "A_BIST_ADDR", zero 8
          ; "A_BIST_DIN", zero 16
          ; "A_BIST_BM", zero 16
          ]
        ~outputs:[ "A_DOUT", 16 ]
    in
    Instantiation.output inst "A_DOUT"

  (* Refuse arbitrary mappings rather than guessing their pin interface or
     quietly building flops the selection did not ask for. *)
  | Implementation, Macro macro ->
    raise_s
      [%message
        "single-port RAM macro backend is unavailable; select flops for this instance"
          (instance : Resource.Id.t)
          (macro : string)]
;;

[@@@ocamlformat "enable"]
