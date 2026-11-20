(** Hardcaml ASIC: portable resources, flow selection, platform templates and project
    elaboration. Groups depend on each other in one direction only: [Resource] and
    [Flow], then [Platform], then [Elaboration], then the root modules ([Chip_target],
    [Methodology_plan], [Single_port_ram]). *)

(** {1 Groups} *)

module Resource = Resource
module Platform = Platform
module Elaboration = Elaboration

(** Flow selection, explicit provider-stage declarations, schema-3 recipes and
    named-flow plan serialization. Includes [Flow.Config] so [Flow.librelane],
    [Flow.validate], [Flow.t] and [Flow.Operation] keep working, and re-exports the
    catalog modules from [Hardcaml_asic_flow_domain]. [Stage] and [Recipe] are separate
    from the project adapter/operation selection in [Config]. *)
module Flow = struct
  include Flow
  include Flow.Config
  module Catalog = Hardcaml_asic_flow_domain.Catalog
  module Methodology = Hardcaml_asic_flow_domain.Methodology
  module Profile = Hardcaml_asic_flow_domain.Profile
  module Selection = Hardcaml_asic_flow_domain.Selection
  module Parameter = Hardcaml_asic_flow_domain.Parameter
end

(** {1 Consumer entry points} *)

(** A single independently elaborated chip. [Project] is the compatible spelling. *)
module Chip_target = Chip_target
module Project = Chip_target
module Chip_catalog = Chip_catalog
module Hardware_variant = Hardware_variant
module Resource_preparation = Resource_preparation
module Resource_snapshot = Elaboration.Resource_snapshot
module Source_snapshot = Elaboration.Source_snapshot
module Methodology_plan = Methodology_plan
module Single_port_ram = Single_port_ram
module Elaborated_project = Elaboration.Elaborated_project
module Target = Platform.Target
module Tt_cmos5l = Platform.Tt_cmos5l
module Pinout = Platform.Pinout
module Clock = Platform.Clock
module Timing = Platform.Timing
module Resource_policy = Resource.Policy
module Technology = Resource.Technology
module Resolved_flow = Elaboration.Resolved
module Bundle = Elaboration.Bundle
module Flow_spec = Flow.Spec
module Macro_intent = Flow.Macro_intent
