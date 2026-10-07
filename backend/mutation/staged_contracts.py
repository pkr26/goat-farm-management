"Verified native and public contract oracles for backend domain mutations."

import ast

FUNCTION_ORACLES = {
    ("app/api/animals.py", "_change_status_mutation"): (
        (
            "tests/test_mutation38_livestock_sale_boundaries.py::test_pub"
            "lic_derived_sale_books_exact_paise_through_the_inclusive_bil"
            "lion_rupee_cap"
        ),
        (
            "tests/test_mutation38_livestock_sale_boundaries.py::test_pub"
            "lic_male_sale_opens_at_eight_calendar_months_and_explains_th"
            "e_literal_gate"
        ),
        (
            "tests/test_mutation38_livestock_sale_boundaries.py::test_pub"
            "lic_male_sale_weight_advisory_is_strictly_below_twenty_four_"
            "kg"
        ),
        (
            "tests/test_mutation38_livestock_sale_boundaries.py::test_pub"
            "lic_sale_composes_operator_note_and_weight_advice_at_literal"
            "_255_characters"
        ),
        (
            "tests/test_mutation38_livestock_status_breeding_race.py::tes"
            "t_backdated_sale_waits_for_a_new_service_and_preserves_its_c"
            "linical_graph"
        ),
        (
            "tests/test_mutation38_livestock_terminal_closure_rejection.p"
            "y::test_public_terminal_status_preserves_422_service_rejecti"
            "on_and_atomic_same_key_retry"
        ),
        (
            "tests/test_mutation38_livestock_terminal_litter_scope.py::te"
            "st_terminal_dam_status_ignores_an_unrelated_real_delivery_an"
            "d_weaning_history"
        ),
        (
            "tests/test_mutation38_livestock_terminal_resolved_pregnancy_"
            "reads.py::test_public_dam_retirement_does_not_wait_on_indepe"
            "ndently_pinned_delivered_history"
        ),
        (
            "tests/test_mutation38_livestock_terminal_restored_service_sw"
            "eep.py::test_retirement_resolves_an_earlier_restored_pending"
            "_snapshot_and_its_real_confirmation"
        ),
        (
            "tests/test_mutation38_livestock_terminal_status_restrictions"
            ".py::test_public_dependent_kid_death_returns_409_and_rolls_b"
            "ack_while_dam_is_pinned"
        ),
        (
            "tests/test_mutation38_livestock_terminal_status_restrictions"
            ".py::test_public_sale_and_cull_respect_a_restored_general_re"
            "gulatory_hold_without_suspicion"
        ),
    ),
    ("app/api/animals.py", "create_animal"): (
        (
            "tests/test_mutation38_livestock_historical_purchase_ledger.p"
            "y::test_historical_purchase_ledger_names_the_recorded_seller"
            "_only_when_present"
        ),
        (
            "tests/test_mutation38_livestock_historical_reason_capacity.p"
            "y::test_historical_import_retains_the_full_255_character_ini"
            "tial_move_audit"
        ),
        (
            "tests/test_mutation38_livestock_registration_weight_cap.py::"
            "test_registration_accepts_the_literal_150_kg_adult_cap_and_r"
            "ejects_above_it"
        ),
        (
            "tests/test_mutation38_livestock_small_entry_weights.py::test"
            "_registration_persists_positive_entry_weights_at_and_below_o"
            "ne_kg"
        ),
        (
            "tests/test_mutation38_livestock_tag_retry_budget.py::test_ex"
            "plicit_namespace_conflict_returns_400_while_next_writer_hold"
            "s_namespace"
        ),
        (
            "tests/test_mutation38_livestock_tag_retry_budget.py::test_tw"
            "o_generated_namespace_collisions_fail_without_a_third_regist"
            "ration"
        ),
    ),
    ("app/api/animals.py", "move_bucket"): (
        (
            "tests/test_mutation38_livestock_move_history_contracts.py::t"
            "est_cleared_orphan_owner_history_correction_keeps_override_c"
            "ontext"
        ),
        (
            "tests/test_mutation38_livestock_move_history_contracts.py::t"
            "est_owner_history_move_retains_the_255th_audit_character"
        ),
        (
            "tests/test_mutation38_livestock_orphan_history_isolation.py:"
            ":test_cleared_orphan_weans_despite_an_unrelated_kids_recover"
            "y_exit"
        ),
        (
            "tests/test_mutation38_livestock_orphan_restored_provenance.p"
            "y::test_cleared_orphan_weans_with_duplicate_restored_links_t"
            "o_the_same_birth"
        ),
        (
            "tests/test_mutation38_livestock_residency_read_budget.py::te"
            "st_breeding_admission_reads_residency_only_for_the_postpartu"
            "m_cohort"
        ),
    ),
    ("app/api/animals.py", "record_weight"): (
        (
            "tests/test_mutation38_livestock_weight_sale_race.py::test_we"
            "ight_waiting_for_a_sale_rejects_the_committed_sold_state"
        ),
    ),
    ("app/api/animals.py", "update_animal"): (
        (
            "tests/test_mutation38_livestock_phenotype_sale_race.py::test"
            "_phenotype_edit_waiting_for_a_sale_rejects_the_committed_sol"
            "d_state"
        ),
    ),
    ("app/api/breeding.py", "_get_breeding_record"): (
        (
            "tests/test_mutation38_breeding_read_and_output_contracts.py:"
            ":test_breeding_detail_reads_the_committed_pregnancy_while_a_"
            "real_abort_is_uncommitted"
        ),
        (
            "tests/test_mutation38_livestock_breeding_record_ids.py::test"
            "_restored_record_identity_boundaries_preserve_public_read_an"
            "d_scan_contracts"
        ),
    ),
    ("app/api/breeding.py", "_lock_doe_then_breeding_record"): (
        (
            "tests/test_mutation38_livestock_breeding_record_ids.py::test"
            "_reserved_record_identity_rejects_without_waiting_for_a_live"
            "_doe_transaction"
        ),
        (
            "tests/test_mutation38_livestock_breeding_record_ids.py::test"
            "_restored_record_identity_boundaries_preserve_public_read_an"
            "d_scan_contracts"
        ),
        (
            "tests/test_mutation38_livestock_pregnancy_write_ownership.py"
            "::test_native_clinical_fetch_retains_exclusive_pregnancy_wri"
            "te_ownership"
        ),
    ),
    ("app/api/breeding.py", "create_breeding"): (
        (
            "tests/test_mutation38_livestock_breeding_ids.py::test_breedi"
            "ng_identity_endpoints_preserve_supported_stored_participants"
        ),
    ),
    ("app/api/breeding.py", "submit_ultrasound"): (
        (
            "tests/test_mutation38_ultrasound_farm_calendar.py::test_ultr"
            "asound_farm_future_is_a_422_and_does_not_consume_the_pending"
            "_service"
        ),
    ),
    ("app/api/buckets.py", "buckets_board"): (
        (
            "tests/test_mutation38_livestock_bucket_board_contracts.py::t"
            "est_bucket_board_preserves_own_active_headcount_and_feed_ove"
            "rrides"
        ),
        (
            "tests/test_mutation38_livestock_bucket_board_contracts.py::t"
            "est_bucket_board_same_day_arrival_has_zero_completed_cohort_"
            "days"
        ),
        (
            "tests/test_mutation38_livestock_bucket_board_contracts.py::t"
            "est_bucket_board_uses_each_animals_own_business_effective_mo"
            "ve_date"
        ),
    ),
    ("app/api/dashboard.py", "_kidding_due_preview"): (
        (
            "tests/test_mutation38_dashboard_native_contract_v4.py::test_"
            "native_kidding_preview_balances_backlog_with_today_and_fourt"
            "een_day_edge"
        ),
    ),
    ("app/api/dashboard.py", "_side"): (
        (
            "tests/test_mutation38_dashboard_single_kidding_native_contra"
            "ct_v1.py::test_single_actual_due_pregnancy_completes_and_ret"
            "ains_its_identity"
        ),
    ),
    ("app/api/dashboard.py", "_task_preview"): (
        (
            "tests/test_mutation38_dashboard_native_contract_v4.py::test_"
            "native_pending_ultrasound_and_policy_horizons_keep_exact_edg"
            "es"
        ),
    ),
    ("app/api/dashboard.py", "dashboard"): (
        (
            "tests/test_mutation38_dashboard_native_contract_v4.py::test_"
            "native_empty_dashboard_and_a_retained_hold_preserve_zero_and"
            "_reason"
        ),
        (
            "tests/test_mutation38_dashboard_native_contract_v4.py::test_"
            "native_pending_ultrasound_and_policy_horizons_keep_exact_edg"
            "es"
        ),
        (
            "tests/test_mutation38_dashboard_native_contract_v4.py::test_"
            "recent_native_weights_keep_ten_observations_and_their_own_id"
            "entities"
        ),
        (
            "tests/test_mutation38_dashboard_retained_catalog_native_cont"
            "ract_v1.py::test_retained_animals_do_not_gain_a_phantom_coun"
            "t_from_an_absent_definition"
        ),
    ),
    ("app/api/dashboard.py", "reports"): (
        (
            "tests/test_mutation38_dashboard_native_contract_v4.py::test_"
            "native_reports_reconcile_live_litter_average_and_one_kilogra"
            "m_weight"
        ),
    ),
    ("app/api/feeding.py", "add_stock"): (
        (
            "tests/test_mutation38_livestock_feeding_http_boundaries.py::"
            "test_stock_add_keeps_reserved_zero_and_accepts_restored_int4"
            "_ceiling_identity"
        ),
    ),
    ("app/api/feeding.py", "dispense"): (
        (
            "tests/test_mutation38_livestock_feeding_http_boundaries.py::"
            "test_actual_dispensing_accepts_omitted_and_explicit_current_"
            "farm_date"
        ),
    ),
    ("app/api/feeding.py", "feeding_history"): (
        (
            "tests/test_mutation38_livestock_feeding_http_boundaries.py::"
            "test_feed_history_exact_inclusive_dates_omitted_bounds_and_d"
            "efault_page"
        ),
    ),
    ("app/api/feeding.py", "list_finished_stock"): (
        (
            "tests/test_mutation38_livestock_feeding_http_boundaries.py::"
            "test_actual_finished_mix_stock_and_raw_inventory_stay_in_the"
            "ir_own_farm"
        ),
    ),
    ("app/api/feeding.py", "list_inventory"): (
        (
            "tests/test_mutation38_livestock_feed_inventory_scope.py::tes"
            "t_real_seeded_raw_inventory_is_complete_and_farm_local"
        ),
    ),
    ("app/api/feeding.py", "mix_batch"): (
        (
            "tests/test_mutation38_livestock_feeding_http_boundaries.py::"
            "test_actual_finished_mix_stock_and_raw_inventory_stay_in_the"
            "ir_own_farm"
        ),
    ),
    ("app/api/finance.py", "_corrected_feed_unit_price"): (
        (
            "tests/test_mutation38_finance_api_contracts.py::test_correct"
            "ed_feed_price_admits_its_boundaries_and_rejects_only_above_t"
            "hem[above-unit-ceiling]"
        ),
        (
            "tests/test_mutation38_finance_api_contracts.py::test_correct"
            "ed_feed_price_admits_its_boundaries_and_rejects_only_above_t"
            "hem[explicit-free-stock]"
        ),
        (
            "tests/test_mutation38_finance_api_contracts.py::test_correct"
            "ed_feed_price_admits_its_boundaries_and_rejects_only_above_t"
            "hem[one-paise-floor]"
        ),
        (
            "tests/test_mutation38_finance_api_contracts.py::test_correct"
            "ed_feed_price_admits_its_boundaries_and_rejects_only_above_t"
            "hem[unit-price-ceiling]"
        ),
        (
            "tests/test_mutation38_finance_api_contracts.py::test_native_"
            "feed_price_helper_preserves_absent_optional_provenance[no-qu"
            "antity]"
        ),
    ),
    ("app/api/finance.py", "_latest_active_feed_purchase_stmt"): (
        (
            "tests/test_mutation38_finance_api_contracts.py::test_three_f"
            "eed_purchases_reconcile_against_the_single_newest_source_cha"
            "in"
        ),
    ),
    ("app/api/finance.py", "_locked_source_animal"): (
        (
            "tests/test_mutation38_finance_api_contracts.py::test_finance"
            "_animal_helpers_preserve_the_full_declared_key_band[largest-"
            "int4]"
        ),
        (
            "tests/test_mutation38_finance_api_contracts.py::test_finance"
            "_animal_helpers_preserve_the_full_declared_key_band[restored"
            "-zero]"
        ),
        (
            "tests/test_mutation38_finance_api_contracts.py::test_missing"
            "_source_animal_returns_the_declared_conflict_without_a_null_"
            "dereference"
        ),
    ),
    ("app/api/finance.py", "_policy_out"): (
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_i"
            "nsurance_register_has_real_filtered_rows_and_asserted_succes"
            "s_responses"
        ),
    ),
    ("app/api/finance.py", "_reconcile_feed_purchase"): (
        (
            "tests/test_mutation38_finance_api_contracts.py::test_correct"
            "ed_feed_price_admits_its_boundaries_and_rejects_only_above_t"
            "hem[above-unit-ceiling]"
        ),
        (
            "tests/test_mutation38_finance_api_contracts.py::test_feed_qu"
            "antity_correction_admits_the_actual_nonnegative_remaining_st"
            "ock[exactly-empty-stock]"
        ),
        (
            "tests/test_mutation38_finance_api_contracts.py::test_legacy_"
            "feed_purchase_rejects_quantity_correction_with_the_declared_"
            "status"
        ),
        (
            "tests/test_mutation38_finance_api_contracts.py::test_native_"
            "hand_repair_rejects_partial_feed_provenance_before_reconcili"
            "ation"
        ),
        (
            "tests/test_mutation38_finance_api_contracts.py::test_native_"
            "latest_feed_purchase_rejects_missing_price_in_a_retained_rep"
            "air"
        ),
    ),
    ("app/api/finance.py", "_reconcile_source_record"): (
        (
            "tests/test_mutation38_livestock_finance_retained_duplicate_f"
            "acts.py::test_public_purchase_correction_keeps_same_fact_dat"
            "e_with_duplicate_retained_initial_move"
        ),
        (
            "tests/test_mutation38_livestock_finance_retained_duplicate_f"
            "acts.py::test_public_same_day_sale_correction_preserves_dupl"
            "icate_retained_auto_abort_facts"
        ),
    ),
    ("app/api/finance.py", "_resolve_related_animal"): (
        (
            "tests/test_mutation38_finance_api_contracts.py::test_finance"
            "_animal_helpers_preserve_the_full_declared_key_band[largest-"
            "int4]"
        ),
        (
            "tests/test_mutation38_finance_api_contracts.py::test_finance"
            "_animal_helpers_preserve_the_full_declared_key_band[restored"
            "-zero]"
        ),
    ),
    ("app/api/finance.py", "add_insurance_policy"): (
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_i"
            "nsurance_register_has_real_filtered_rows_and_asserted_succes"
            "s_responses"
        ),
    ),
    ("app/api/finance.py", "animal_lifetime_pnl"): (
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_l"
            "ifetime_pnl_preserves_its_actual_animal_route_key_band[large"
            "st-int4]"
        ),
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_l"
            "ifetime_pnl_preserves_its_actual_animal_route_key_band[resto"
            "red-zero]"
        ),
    ),
    ("app/api/finance.py", "claim_policy"): (
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_c"
            "laim_and_history_preserve_actual_policy_link_and_route_key_b"
            "and[first-live-id]"
        ),
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_c"
            "laim_and_history_preserve_actual_policy_link_and_route_key_b"
            "and[largest-int4]"
        ),
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_c"
            "laim_and_history_preserve_actual_policy_link_and_route_key_b"
            "and[restored-zero]"
        ),
    ),
    ("app/api/finance.py", "insurance_policy_history"): (
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_c"
            "laim_and_history_preserve_actual_policy_link_and_route_key_b"
            "and[largest-int4]"
        ),
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_c"
            "laim_and_history_preserve_actual_policy_link_and_route_key_b"
            "and[restored-zero]"
        ),
    ),
    ("app/api/finance.py", "list_insurance_policies"): (
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_i"
            "nsurance_query_gate_rejects_only_outside_its_declared_envelo"
            "pe[zero-animal-filter]"
        ),
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_i"
            "nsurance_register_has_real_filtered_rows_and_asserted_succes"
            "s_responses"
        ),
    ),
    ("app/api/finance.py", "list_transactions"): (
        (
            "tests/test_mutation38_finance_api_contracts.py::test_correct"
            "ion_preserves_narrative_and_default_first_page"
        ),
        (
            "tests/test_mutation38_finance_api_contracts.py::test_finance"
            "_summary_exposes_mortality_only_to_a_health_reader"
        ),
    ),
    ("app/api/finance.py", "mutate"): (
        (
            "tests/test_mutation38_finance_api_contracts.py::test_correct"
            "ion_preserves_narrative_and_default_first_page"
        ),
        (
            "tests/test_mutation38_finance_api_contracts.py::test_insuran"
            "ce_renewal_preserves_its_declared_route_key_band[largest-int"
            "4]"
        ),
        (
            "tests/test_mutation38_finance_api_contracts.py::test_insuran"
            "ce_renewal_preserves_its_declared_route_key_band[restored-ze"
            "ro]"
        ),
        (
            "tests/test_mutation38_finance_api_contracts.py::test_manual_"
            "ledger_correction_rejects_a_feed_only_quantity_override"
        ),
        (
            "tests/test_mutation38_finance_api_contracts.py::test_transac"
            "tion_correction_preserves_its_declared_route_key_band[larges"
            "t-int4]"
        ),
        (
            "tests/test_mutation38_finance_api_contracts.py::test_transac"
            "tion_correction_preserves_its_declared_route_key_band[restor"
            "ed-zero]"
        ),
    ),
    ("app/api/health.py", "_bulk_target_snapshot"): (
        (
            "tests/test_mutation38_health_bulk_preview.py::test_batch_pre"
            "view_admits_the_largest_persistable_batch_key"
        ),
        (
            "tests/test_mutation38_health_bulk_preview.py::test_bucket_pr"
            "eview_accepts_exact_capacity_and_rejects_an_extra_animal"
        ),
        (
            "tests/test_mutation38_health_bulk_preview.py::test_linked_pr"
            "eview_admits_a_constraint_valid_restored_maximum_task_key"
        ),
        (
            "tests/test_mutation38_health_bulk_preview.py::test_preview_r"
            "ejects_nonrequired_round_components_and_mismatched_batch_dut"
            "ies"
        ),
        (
            "tests/test_mutation38_health_bulk_preview.py::test_round_pre"
            "view_preserves_declared_members_exclusions_and_component_evi"
            "dence"
        ),
    ),
    ("app/api/health.py", "_change_round_targets"): (
        (
            "tests/test_mutation38_livestock_health_round_changes.py::tes"
            "t_round_exclusion_replays_require_exact_actor_reason_and_con"
            "tinue_to_remaining_targets"
        ),
        (
            "tests/test_mutation38_livestock_health_round_changes.py::tes"
            "t_round_inclusion_replays_require_exact_actor_reason_and_con"
            "tinue_to_new_arrivals"
        ),
        (
            "tests/test_mutation38_livestock_health_round_changes.py::tes"
            "t_round_target_admission_preserves_start_tenant_active_and_p"
            "ending_guards"
        ),
    ),
    ("app/api/health.py", "_lock_event_targets"): (
        (
            "tests/test_mutation38_livestock_health_event_boundaries.py::"
            "test_exact_250_animal_reviewed_bucket_cohort_can_be_recorded"
        ),
        (
            "tests/test_mutation38_livestock_health_event_boundaries.py::"
            "test_linked_batch_scope_rejection_is_422_and_preserves_its_r"
            "eal_pending_duty"
        ),
        (
            "tests/test_mutation38_livestock_health_event_boundaries.py::"
            "test_public_health_event_and_schedule_accept_the_real_int4_c"
            "eiling_animal"
        ),
    ),
    ("app/api/health.py", "_record_event_mutation"): (
        (
            "tests/test_mutation38_health_event_assignee_pin.py::test_lin"
            "ked_health_evidence_pins_the_named_assignee_during_peer_fall"
            "back"
        ),
        (
            "tests/test_mutation38_livestock_health_alert_integrity.py::t"
            "est_real_health_alerts_preserve_suspicion_message_and_single"
            "_delivery_on_replay"
        ),
        (
            "tests/test_mutation38_livestock_health_event_boundaries.py::"
            "test_explicit_health_schedule_template_accepts_its_own_targe"
            "t_and_rejects_another"
        ),
        (
            "tests/test_mutation38_livestock_health_event_boundaries.py::"
            "test_omitted_health_date_keeps_the_literal_ten_year_next_due"
            "_boundary"
        ),
        (
            "tests/test_mutation38_livestock_health_missing_catalogue.py:"
            ":test_inferred_missing_reference_template_is_422_and_retries"
            "_after_catalogue_repair"
        ),
    ),
    ("app/api/health.py", "_round_out"): (
        (
            "tests/test_mutation38_health_round_progress_contracts.py::te"
            "st_retained_completed_round_has_no_invented_snapshot_or_coun"
            "ts"
        ),
        (
            "tests/test_mutation38_health_round_progress_contracts.py::te"
            "st_round_progress_keeps_exclusions_with_their_exact_cohort_a"
            "nd_member"
        ),
    ),
    ("app/api/health.py", "_round_task"): (
        (
            "tests/test_mutation38_health_round_progress_contracts.py::te"
            "st_round_lookup_obeys_positive_int4_boundary_for_real_retain"
            "ed_rows"
        ),
        (
            "tests/test_mutation38_health_round_read_availability.py::tes"
            "t_public_round_progress_does_not_wait_for_the_owned_task_wri"
            "te_mutex"
        ),
    ),
    ("app/api/health.py", "_writable_round_task"): (
        (
            "tests/test_mutation38_health_round_lock_contracts.py::test_r"
            "ound_start_pins_the_inactive_named_assignee_during_peer_fall"
            "back"
        ),
        (
            "tests/test_mutation38_health_round_lock_contracts.py::test_r"
            "ound_start_rechecks_a_genuine_concurrent_public_skip"
        ),
        (
            "tests/test_mutation38_livestock_health_round_changes.py::tes"
            "t_health_manager_cannot_change_another_workers_assigned_herd"
            "_round"
        ),
    ),
    ("app/api/health.py", "add_health_round_targets"): (
        (
            "tests/test_mutation38_livestock_health_round_changes.py::tes"
            "t_round_target_admission_preserves_start_tenant_active_and_p"
            "ending_guards"
        ),
    ),
    ("app/api/health.py", "clear_movement_restriction"): (
        (
            "tests/test_mutation38_livestock_health_restriction_boundarie"
            "s.py::test_clear_keeps_a_retained_late_notification_absent_f"
            "rom_the_immutable_event"
        ),
        (
            "tests/test_mutation38_livestock_health_restriction_boundarie"
            "s.py::test_restriction_paths_reserve_zero_even_when_legacy_s"
            "torage_has_that_identity"
        ),
        (
            "tests/test_mutation38_livestock_health_restriction_boundarie"
            "s.py::test_restriction_reads_and_clear_accept_the_public_int"
            "4_ceiling"
        ),
        (
            "tests/test_mutation38_livestock_health_restriction_boundarie"
            "s.py::test_retained_general_hold_is_active_and_can_be_refere"
            "nced_cleared"
        ),
    ),
    ("app/api/health.py", "health_animal_options"): (
        (
            "tests/test_mutation38_livestock_health_picker_boundaries.py:"
            ":test_health_animal_picker_discloses_a_retained_general_regu"
            "latory_hold"
        ),
        (
            "tests/test_mutation38_livestock_health_picker_boundaries.py:"
            ":test_health_animal_search_accepts_the_actual_int4_ceiling_i"
            "dentity"
        ),
        (
            "tests/test_mutation38_livestock_health_picker_boundaries.py:"
            ":test_health_pickers_accept_omitted_empty_and_whitespace_sea"
            "rch"
        ),
    ),
    ("app/api/health.py", "health_purchase_batch_options"): (
        (
            "tests/test_mutation38_livestock_health_picker_boundaries.py:"
            ":test_health_batch_search_accepts_the_actual_int4_ceiling_id"
            "entity"
        ),
        (
            "tests/test_mutation38_livestock_health_picker_boundaries.py:"
            ":test_health_pickers_accept_omitted_empty_and_whitespace_sea"
            "rch"
        ),
    ),
    ("app/api/health.py", "health_round_progress"): (
        (
            "tests/test_mutation38_health_round_progress_contracts.py::te"
            "st_retained_completed_round_has_no_invented_snapshot_or_coun"
            "ts"
        ),
    ),
    ("app/api/health.py", "list_events"): (
        (
            "tests/test_mutation38_health_event_feed_contract.py::test_he"
            "alth_event_feed_returns_own_fact_and_keeps_foreign_fact_out"
        ),
    ),
    ("app/api/health.py", "movement_restriction_history"): (
        (
            "tests/test_mutation38_livestock_health_restriction_boundarie"
            "s.py::test_restriction_paths_reserve_zero_even_when_legacy_s"
            "torage_has_that_identity"
        ),
        (
            "tests/test_mutation38_livestock_health_restriction_boundarie"
            "s.py::test_restriction_reads_and_clear_accept_the_public_int"
            "4_ceiling"
        ),
        (
            "tests/test_mutation38_livestock_health_restriction_boundarie"
            "s.py::test_retained_general_hold_is_active_and_can_be_refere"
            "nced_cleared"
        ),
    ),
    ("app/api/health.py", "record_event"): (
        (
            "tests/test_mutation38_livestock_health_alert_integrity.py::t"
            "est_real_health_alerts_preserve_suspicion_message_and_single"
            "_delivery_on_replay"
        ),
    ),
    ("app/api/health.py", "start_health_round"): (
        (
            "tests/test_mutation38_health_round_lock_contracts.py::test_r"
            "ound_start_keeps_animal_before_task_order_against_real_healt"
            "h_evidence"
        ),
        (
            "tests/test_mutation38_health_round_lock_contracts.py::test_s"
            "napshot_remains_available_during_a_native_nonkey_prepartum_m"
            "ove"
        ),
        (
            "tests/test_mutation38_livestock_health_round_changes.py::tes"
            "t_round_start_rejects_an_unrecognized_native_programme_as_42"
            "2"
        ),
    ),
    ("app/api/health.py", "vaccination_schedule"): (
        (
            "tests/test_mutation38_livestock_health_event_boundaries.py::"
            "test_public_health_event_and_schedule_accept_the_real_int4_c"
            "eiling_animal"
        ),
        (
            "tests/test_mutation38_livestock_health_event_boundaries.py::"
            "test_schedule_keeps_reserved_zero_not_found_for_a_constraint"
            "_valid_legacy_animal"
        ),
    ),
    ("app/api/kidding.py", "create_kidding"): (
        (
            "tests/test_mutation38_livestock_kidding_creation_ids.py::tes"
            "t_kidding_creation_accepts_actual_restored_int4_ceiling"
        ),
        (
            "tests/test_mutation38_livestock_kidding_creation_ids.py::tes"
            "t_kidding_creation_id_above_int4_returns404"
        ),
        (
            "tests/test_mutation38_livestock_kidding_parent_progress.py::"
            "test_real_kidding_and_rebreeding_complete_with_one_litter_an"
            "d_one_live_kid"
        ),
        (
            "tests/test_mutation38_livestock_kidding_restore_retention.py"
            "::test_kidding_returns404_when_native_retention_removes_unli"
            "nked_restored_provenance"
        ),
        (
            "tests/test_mutation38_livestock_kidding_wire.py::test_anothe"
            "r_farms_stillborn_tag_does_not_reserve_this_farms_namespace"
        ),
        (
            "tests/test_mutation38_livestock_kidding_wire.py::test_kid_bi"
            "rth_weight_accepts_half_a_kilo_and_reports_the_literal_credi"
            "ble_band"
        ),
        (
            "tests/test_mutation38_livestock_kidding_wire.py::test_restor"
            "ed_dam_birth_date_equal_to_delivery_is_not_before_birth"
        ),
    ),
    ("app/api/kidding.py", "kidding_list"): (
        (
            "tests/test_mutation38_livestock_kidding_wire.py::test_kiddin"
            "g_pages_default_to_thirty_rows_and_zero_offsets"
        ),
    ),
    ("app/api/kidding.py", "kidding_pregnancy"): (
        (
            "tests/test_mutation38_livestock_kidding_wire.py::test_kiddin"
            "g_pregnancy_links_apply_literal_int4_id_boundaries_to_restor"
            "ed_rows"
        ),
        (
            "tests/test_mutation38_livestock_kidding_wire.py::test_kiddin"
            "g_pregnancy_missing_and_out_of_range_links_return404"
        ),
        (
            "tests/test_mutation38_livestock_kidding_wire.py::test_live_p"
            "regnancy_lookup_ignores_another_does_completed_litter"
        ),
    ),
    ("app/api/ops_simulation.py", "run_daily_ops_simulation"): (
        (
            "tests/test_mutation38_simulation_daily_api_admission_account"
            "ing_v3.py::test_above_head_day_ceiling_rejects_before_chargi"
            "ng_budget"
        ),
        (
            "tests/test_mutation38_simulation_daily_api_admission_account"
            "ing_v3.py::test_completed_daily_run_books_its_documented_amp"
            "lified_work"
        ),
        (
            "tests/test_mutation38_simulation_daily_api_admission_account"
            "ing_v3.py::test_head_day_ceiling_admits_exactly_fifty_thousa"
            "nd"
        ),
    ),
    ("app/api/ops_simulation.py", "work"): (
        (
            "tests/test_mutation38_simulation_daily_api_admission_account"
            "ing_v3.py::test_real_births_that_outgrow_result_ceiling_rece"
            "ive_input_rejection"
        ),
        (
            "tests/test_mutation38_simulation_daily_api_admission_account"
            "ing_v3.py::test_valid_daily_run_returns_only_the_requested_a"
            "udit_ledger"
        ),
    ),
    ("app/api/owner.py", "_rate"): (
        (
            "tests/test_mutation38_dashboard_native_contract_v4.py::test_"
            "owner_percentages_keep_physical_units_and_absent_observation"
            "s"
        ),
    ),
    ("app/api/owner.py", "owner_benchmarks"): (
        (
            "tests/test_mutation38_dashboard_native_contract_v4.py::test_"
            "owner_default_window_is_ninety_actual_calendar_days"
        ),
        (
            "tests/test_mutation38_owner_benchmarks_native_contract_v1.py"
            "::test_real_owner_benchmarks_keep_window_edges_and_report_ph"
            "ysical_rates"
        ),
        (
            "tests/test_mutation38_owner_residual_native_contract_v4.py::"
            "test_one_day_half_kilogram_gain_keeps_zero_and_absent_feed_l"
            "edgers"
        ),
        (
            "tests/test_mutation38_owner_residual_native_contract_v4.py::"
            "test_owned_birth_and_service_windows_use_their_own_real_cale"
            "ndar"
        ),
    ),
    ("app/api/owner.py", "owner_overview"): (
        (
            "tests/test_mutation38_owner_overview_native_contract_v2.py::"
            "test_real_distinct_owner_rollups_reconcile_all_three_owned_f"
            "arms"
        ),
        (
            "tests/test_mutation38_owner_residual_native_contract_v4.py::"
            "test_owner_overview_no_transactions_has_zero_real_net"
        ),
    ),
    ("app/api/planner.py", "_assumptions_json_at_anchor"): (
        (
            "tests/test_mutation38_saved_decision_native_crud_v2.py::test"
            "_plan_anchor_normalization_preserves_the_callers_validated_d"
            "ocument"
        ),
    ),
    ("app/api/planner.py", "_check_name_free"): (
        (
            "tests/test_mutation38_saved_decision_native_crud_v2.py::test"
            "_saved_decision_name_preflight_detects_the_first_native_row"
        ),
    ),
    ("app/api/planner.py", "_get_plan"): (
        (
            "tests/test_mutation38_saved_decision_native_concurrency_v3.p"
            "y::test_invalid_zero_identifier_is_rejected_before_saved_tab"
            "le_access"
        ),
        (
            "tests/test_mutation38_saved_decision_native_concurrency_v3.p"
            "y::test_locking_fetch_refreshes_real_session_identity_cache"
        ),
        (
            "tests/test_mutation38_saved_decision_native_concurrency_v3.p"
            "y::test_saved_decision_read_does_not_acquire_an_unrequested_"
            "writer_lock"
        ),
        (
            "tests/test_mutation38_saved_decision_native_crud_v2.py::test"
            "_last_postgresql_generated_decision_identifier_is_readable"
        ),
    ),
    ("app/api/planner.py", "_load_plan_parts"): (
        (
            "tests/test_mutation38_saved_decision_native_crud_v2.py::test"
            "_retained_plan_is_listed_recoverably_and_rejected_for_detail"
        ),
    ),
    ("app/api/planner.py", "_plan_out"): (
        (
            "tests/test_mutation38_saved_decision_native_crud_v2.py::test"
            "_retained_plan_is_listed_recoverably_and_rejected_for_detail"
        ),
    ),
    ("app/api/planner.py", "build"): (
        (
            "tests/test_mutation38_simulation_dpr_finite_response_v2.py::"
            "test_dpr_rejects_actual_unrepresentable_return_with_its_vali"
            "dation_status"
        ),
    ),
    ("app/api/planner.py", "create_plan"): (
        (
            "tests/test_mutation38_saved_decision_native_crud_v2.py::test"
            "_saved_decision_whitespace_name_has_bad_request_status"
        ),
    ),
    ("app/api/planner.py", "delete_plan"): (
        (
            "tests/test_mutation38_saved_decision_native_concurrency_v3.p"
            "y::test_saved_decision_racing_revision_is_checked_after_row_"
            "lock"
        ),
    ),
    ("app/api/planner.py", "list_plans"): (
        (
            "tests/test_mutation38_saved_decision_native_concurrency_v3.p"
            "y::test_retained_plan_list_accepts_jsonb_valid_legacy_target"
            "_validation_failure"
        ),
    ),
    ("app/api/planner.py", "mutate"): (
        (
            "tests/test_mutation38_saved_decision_native_concurrency_v3.p"
            "y::test_actual_unique_index_race_returns_a_domain_bad_reques"
            "t"
        ),
    ),
    ("app/api/planner.py", "plan_dpr"): (
        (
            "tests/test_mutation38_simulation_dpr_budget.py::test_saved_p"
            "lan_dpr_reserves_the_full_documented_projection_work"
        ),
    ),
    ("app/api/planner.py", "plan_sales"): (
        (
            "tests/test_mutation38_planner_public_admission_boundaries_v2"
            ".py::test_final_twenty_year_month_runs_and_prices_its_full_h"
            "orizon"
        ),
        (
            "tests/test_mutation38_planner_public_admission_boundaries_v2"
            ".py::test_first_month_beyond_twenty_years_rejects_before_nat"
            "ive_charge"
        ),
        (
            "tests/test_mutation38_planner_public_admission_boundaries_v2"
            ".py::test_native_price_matches_deterministic_and_requested_r"
            "isk_passes"
        ),
        (
            "tests/test_mutation38_planner_public_admission_boundaries_v2"
            ".py::test_omitted_risk_replays_are_the_same_zero_risk_plan"
        ),
    ),
    ("app/api/planner.py", "run"): (
        (
            "tests/test_mutation38_planner_public_admission_boundaries_v2"
            ".py::test_valid_tiny_dm_fraction_returns_nonfinite_plan_as_4"
            "22"
        ),
    ),
    ("app/api/planner.py", "update_plan"): (
        (
            "tests/test_mutation38_saved_decision_native_concurrency_v3.p"
            "y::test_actual_unique_index_race_returns_a_domain_bad_reques"
            "t"
        ),
        (
            "tests/test_mutation38_saved_decision_native_concurrency_v3.p"
            "y::test_saved_decision_racing_revision_is_checked_after_row_"
            "lock"
        ),
        (
            "tests/test_mutation38_saved_decision_native_crud_v2.py::test"
            "_retained_plan_is_listed_recoverably_and_rejected_for_detail"
        ),
        (
            "tests/test_mutation38_saved_decision_native_crud_v2.py::test"
            "_saved_decision_legacy_revision_bridge_allows_exactly_one_wr"
            "ite"
        ),
        (
            "tests/test_mutation38_saved_decision_native_crud_v2.py::test"
            "_saved_decision_names_are_scoped_and_self_rename_is_allowed"
        ),
        (
            "tests/test_mutation38_saved_decision_native_crud_v2.py::test"
            "_saved_decision_whitespace_name_has_bad_request_status"
        ),
        (
            "tests/test_mutation38_saved_decision_native_crud_v2.py::test"
            "_saved_plan_each_changed_document_advances_revision"
        ),
        (
            "tests/test_mutation38_saved_decision_native_crud_v2.py::test"
            "_saved_plan_noop_preserves_reviewed_revision"
        ),
        (
            "tests/test_mutation38_saved_plan_assumptions_patch_validity_"
            "v3.py::test_assumptions_only_patch_preserves_valid_plan_and_"
            "authoritative_anchor"
        ),
    ),
    ("app/api/purchases.py", "batch_detail"): (
        (
            "tests/test_mutation38_purchase_detail_contracts.py::test_pur"
            "chase_detail_rejects_zero_even_when_a_legacy_batch_uses_that"
            "_id"
        ),
        (
            "tests/test_mutation38_purchase_detail_contracts.py::test_pur"
            "chase_detail_succeeds_with_the_authorized_profile_and_task_s"
            "cope"
        ),
    ),
    ("app/api/purchases.py", "create_batch"): (
        (
            "tests/test_mutation38_livestock_purchase_service_rejection.p"
            "y::test_purchase_service_rejection_returns_400_rolls_back_an"
            "d_allows_same_key_retry"
        ),
        (
            "tests/test_mutation38_livestock_purchase_wire_contracts.py::"
            "test_purchase_accepts_and_persists_positive_arrival_weight_b"
            "oundaries"
        ),
    ),
    ("app/api/purchases.py", "list_batches"): (
        (
            "tests/test_mutation38_livestock_purchase_wire_contracts.py::"
            "test_purchase_list_default_window_contains_the_first_100_of_"
            "102_batches"
        ),
        (
            "tests/test_mutation38_livestock_purchase_wire_contracts.py::"
            "test_purchase_search_accepts_a_restored_batch_at_the_int4_id"
            "_ceiling"
        ),
    ),
    ("app/api/screening.py", "_batch_progress"): (
        (
            "tests/test_mutation38_screening_batch_contracts.py::test_wal"
            "kthrough_progress_conserves_actual_mixed_and_legacy_image_st"
            "ates"
        ),
    ),
    ("app/api/screening.py", "_has_live_screening_image"): (
        (
            "tests/test_mutation38_screening_read_contracts.py::test_prov"
            "ider_scoreboard_correlates_the_single_live_photo_with_its_ru"
            "n"
        ),
    ),
    ("app/api/screening.py", "_latest_runs_by_image"): (
        (
            "tests/test_mutation38_screening_read_positive_contracts.py::"
            "test_committed_gate_is_present_in_its_own_photo_latest_evide"
            "nce"
        ),
    ),
    ("app/api/screening.py", "_slot"): (
        (
            "tests/test_mutation38_screening_scoreboard_sparse_groups.py:"
            ":test_native_stage_adapter_preserves_actual_gate_and_special"
            "ist_model_cohorts"
        ),
    ),
    ("app/api/screening.py", "_sort_key"): (
        (
            "tests/test_mutation38_screening_scoreboard_hash_order.py::te"
            "st_native_stats_keeps_model_ties_when_postgres_chooses_hash_"
            "aggregation"
        ),
        (
            "tests/test_mutation38_screening_scoreboard_sparse_groups.py:"
            ":test_native_stage_adapter_preserves_actual_gate_and_special"
            "ist_model_cohorts"
        ),
        (
            "tests/test_mutation38_screening_scoreboard_sparse_groups.py:"
            ":test_real_model_version_history_has_stable_provider_then_mo"
            "del_tie_order"
        ),
    ),
    ("app/api/screening.py", "export_dataset"): (
        (
            "tests/test_mutation38_screening_scoreboard_export_contracts."
            "py::test_export_preserves_reviewed_photo_crop_and_actual_pos"
            "itive_model_verdict[cropped-goat]"
        ),
        (
            "tests/test_mutation38_screening_scoreboard_export_contracts."
            "py::test_export_preserves_reviewed_photo_crop_and_actual_pos"
            "itive_model_verdict[whole-frame]"
        ),
    ),
    ("app/api/screening.py", "finding_review_history"): (
        (
            "tests/test_mutation38_screening_vet_adjudication_contracts.p"
            "y::test_vet_review_and_history_admit_positive_int8_ceiling_a"
            "nd_reserve_zero"
        ),
        (
            "tests/test_mutation38_screening_vet_history_contracts.py::te"
            "st_pending_finding_has_no_legacy_decision_and_missing_histor"
            "y_is_private"
        ),
        (
            "tests/test_mutation38_screening_vet_history_contracts.py::te"
            "st_review_history_coexists_with_an_independent_shared_photo_"
            "reader"
        ),
    ),
    ("app/api/screening.py", "get_image"): (
        (
            "tests/test_mutation38_screening_read_contracts.py::test_imag"
            "e_detail_reserves_zero_and_admits_real_int8_ceiling"
        ),
        (
            "tests/test_mutation38_screening_read_contracts.py::test_imag"
            "e_read_coexists_with_an_independent_native_shared_retention_"
            "lease"
        ),
        (
            "tests/test_mutation38_screening_read_contracts.py::test_reta"
            "ined_crop_quality_reads_only_successful_gate_evidence_and_im"
            "mutable_urls"
        ),
        (
            "tests/test_mutation38_screening_read_positive_contracts.py::"
            "test_configured_review_returns_the_actual_normalized_crop_ur"
            "l"
        ),
    ),
    ("app/api/screening.py", "list_batches"): (
        (
            "tests/test_mutation38_screening_batch_contracts.py::test_wal"
            "kthrough_progress_conserves_actual_mixed_and_legacy_image_st"
            "ates"
        ),
    ),
    ("app/api/screening.py", "list_images"): (
        (
            "tests/test_mutation38_screening_read_contracts.py::test_list"
            "_preserves_farm_filters_latest_evidence_and_review_kinds"
        ),
    ),
    ("app/api/screening.py", "mutate"): (
        (
            "tests/test_mutation38_screening_intake_capacity.py::test_abs"
            "ent_walkthrough_upload_returns_the_public_not_found_contract"
        ),
        (
            "tests/test_mutation38_screening_intake_capacity.py::test_exa"
            "ctly_25_hour_old_walkthroughs_still_consume_the_farm_open_qu"
            "ota"
        ),
        (
            "tests/test_mutation38_screening_intake_capacity.py::test_typ"
            "ed_presign_refusal_returns_retryable_503_and_rolls_back_regi"
            "stration"
        ),
        (
            "tests/test_mutation38_screening_intake_capacity.py::test_wal"
            "kthrough_upload_window_includes_the_exact_25_hour_boundary[l"
            "ast-instant]"
        ),
    ),
    ("app/api/screening.py", "provider_stats"): (
        (
            "tests/test_mutation38_screening_crosscheck_presence.py::test"
            "_actual_successful_cross_check_retains_a_visible_scoreboard_"
            "row[healthy-disagreement]"
        ),
        (
            "tests/test_mutation38_screening_reviewed_score_counts.py::te"
            "st_confirmed_actual_positive_has_no_pending_positive_or_heal"
            "thy_controls"
        ),
        (
            "tests/test_mutation38_screening_scoreboard_export_contracts."
            "py::test_scoreboard_counts_each_actual_gate_verdict_without_"
            "fictional_averages[ERROR]"
        ),
        (
            "tests/test_mutation38_screening_scoreboard_export_contracts."
            "py::test_scoreboard_counts_each_actual_gate_verdict_without_"
            "fictional_averages[HEALTHY]"
        ),
        (
            "tests/test_mutation38_screening_scoreboard_export_contracts."
            "py::test_scoreboard_counts_each_actual_gate_verdict_without_"
            "fictional_averages[UNASSESSABLE]"
        ),
        (
            "tests/test_mutation38_screening_scoreboard_export_contracts."
            "py::test_scoreboard_counts_only_successful_actual_cross_chec"
            "ks[healthy-disagreement]"
        ),
        (
            "tests/test_mutation38_screening_scoreboard_export_contracts."
            "py::test_scoreboard_includes_exact_window_edge_and_excludes_"
            "other_farms_and_old_history"
        ),
    ),
    ("app/api/screening.py", "review_finding"): (
        (
            "tests/test_mutation38_screening_vet_adjudication_contracts.p"
            "y::test_existing_migrated_legacy_snapshot_is_preserved_durin"
            "g_a_real_vet_correction"
        ),
        (
            "tests/test_mutation38_screening_vet_adjudication_contracts.p"
            "y::test_vet_adjudication_coexists_with_an_independent_shared"
            "_photo_reader"
        ),
        (
            "tests/test_mutation38_screening_vet_adjudication_contracts.p"
            "y::test_vet_review_and_history_admit_positive_int8_ceiling_a"
            "nd_reserve_zero"
        ),
    ),
    ("app/api/screening.py", "submit_batch"): (
        (
            "tests/test_mutation38_screening_batch_contracts.py::test_rea"
            "l_walkthrough_submission_preserves_the_positive_int4_identif"
            "ier_contract[last]"
        ),
        (
            "tests/test_mutation38_screening_batch_contracts.py::test_rea"
            "l_walkthrough_submission_preserves_the_positive_int4_identif"
            "ier_contract[retained-zero]"
        ),
    ),
    ("app/api/simulation.py", "_check_name_free"): (
        (
            "tests/test_mutation38_saved_decision_native_crud_v2.py::test"
            "_saved_decision_name_preflight_detects_the_first_native_row"
        ),
    ),
    ("app/api/simulation.py", "_get_scenario"): (
        (
            "tests/test_mutation38_saved_decision_native_concurrency_v3.p"
            "y::test_invalid_zero_identifier_is_rejected_before_saved_tab"
            "le_access"
        ),
        (
            "tests/test_mutation38_saved_decision_native_concurrency_v3.p"
            "y::test_locking_fetch_refreshes_real_session_identity_cache"
        ),
        (
            "tests/test_mutation38_saved_decision_native_concurrency_v3.p"
            "y::test_saved_decision_read_does_not_acquire_an_unrequested_"
            "writer_lock"
        ),
        (
            "tests/test_mutation38_saved_decision_native_crud_v2.py::test"
            "_last_postgresql_generated_decision_identifier_is_readable"
        ),
    ),
    ("app/api/simulation.py", "_run"): (
        (
            "tests/test_mutation38_saved_scenario_native_execution_v1.py:"
            ":test_valid_subnormal_dry_matter_fraction_has_a_recoverable_"
            "overflow_response"
        ),
    ),
    ("app/api/simulation.py", "compare_scenarios"): (
        (
            "tests/test_mutation38_saved_scenario_native_execution_v1.py:"
            ":test_compare_accepts_the_last_natively_generated_postgresql"
            "_identifier"
        ),
        (
            "tests/test_mutation38_saved_scenario_native_execution_v1.py:"
            ":test_compare_rejects_zero_as_invalid_input_before_resource_"
            "lookup"
        ),
        (
            "tests/test_mutation38_saved_scenario_native_execution_v1.py:"
            ":test_scenario_compare_spends_only_the_native_deterministic_"
            "request_cost"
        ),
    ),
    ("app/api/simulation.py", "create_scenario"): (
        (
            "tests/test_mutation38_saved_decision_native_crud_v2.py::test"
            "_saved_decision_whitespace_name_has_bad_request_status"
        ),
    ),
    ("app/api/simulation.py", "delete_scenario"): (
        (
            "tests/test_mutation38_saved_decision_native_concurrency_v3.p"
            "y::test_saved_decision_racing_revision_is_checked_after_row_"
            "lock"
        ),
    ),
    ("app/api/simulation.py", "farm_calibration"): (
        (
            "tests/test_mutation38_simulation_calibration_native_evidence"
            "_v3.py::test_empty_native_farm_calibration_returns_zero_open"
            "ing_cohorts_with_auditable_evidence"
        ),
    ),
    ("app/api/simulation.py", "herd_snapshot"): (
        (
            "tests/test_mutation38_simulation_herd_calendar.py::test_publ"
            "ic_herd_snapshot_conserves_each_calendar_boundary_and_unknow"
            "n_age"
        ),
    ),
    ("app/api/simulation.py", "mutate"): (
        (
            "tests/test_mutation38_saved_decision_native_concurrency_v3.p"
            "y::test_actual_unique_index_race_returns_a_domain_bad_reques"
            "t"
        ),
    ),
    ("app/api/simulation.py", "run_compare"): (
        (
            "tests/test_mutation38_saved_scenario_native_execution_v1.py:"
            ":test_scenario_compare_spends_only_the_native_deterministic_"
            "request_cost"
        ),
    ),
    ("app/api/simulation.py", "run_scenario"): (
        (
            "tests/test_mutation38_saved_decision_native_concurrency_v3.p"
            "y::test_saved_scenario_run_captures_revision_after_the_nativ"
            "e_row_lock"
        ),
        (
            "tests/test_mutation38_saved_scenario_native_execution_v1.py:"
            ":test_saved_scenario_default_run_omits_every_optional_analys"
            "is"
        ),
    ),
    ("app/api/simulation.py", "update_scenario"): (
        (
            "tests/test_mutation38_saved_decision_native_concurrency_v3.p"
            "y::test_actual_unique_index_race_returns_a_domain_bad_reques"
            "t"
        ),
        (
            "tests/test_mutation38_saved_decision_native_concurrency_v3.p"
            "y::test_saved_decision_racing_revision_is_checked_after_row_"
            "lock"
        ),
        (
            "tests/test_mutation38_saved_decision_native_concurrency_v3.p"
            "y::test_saved_scenario_noop_preserves_its_reviewed_revision"
        ),
        (
            "tests/test_mutation38_saved_decision_native_crud_v2.py::test"
            "_retained_scenario_notes_write_returns_committed_invalid_sta"
            "te"
        ),
        (
            "tests/test_mutation38_saved_decision_native_crud_v2.py::test"
            "_saved_decision_names_are_scoped_and_self_rename_is_allowed"
        ),
        (
            "tests/test_mutation38_saved_decision_native_crud_v2.py::test"
            "_saved_decision_whitespace_name_has_bad_request_status"
        ),
    ),
    ("app/api/tasks.py", "_get_task"): (
        (
            "tests/test_mutation38_task_default_read_availability.py::tes"
            "t_default_native_task_read_does_not_join_an_existing_writer_"
            "mutex"
        ),
        (
            "tests/test_mutation38_task_identifier_contracts.py::test_act"
            "ual_task_lookup_and_completion_preserve_the_declared_identif"
            "ier_band[largest-int4]"
        ),
        (
            "tests/test_mutation38_task_identifier_contracts.py::test_act"
            "ual_task_lookup_and_completion_preserve_the_declared_identif"
            "ier_band[retained-zero]"
        ),
    ),
    ("app/api/tasks.py", "_lock_completion_animals"): (
        (
            "tests/test_mutation38_task_identifier_contracts.py::test_act"
            "ual_task_lookup_and_completion_preserve_the_declared_identif"
            "ier_band[largest-int4]"
        ),
        (
            "tests/test_mutation38_task_identifier_contracts.py::test_act"
            "ual_task_lookup_and_completion_preserve_the_declared_identif"
            "ier_band[retained-zero]"
        ),
        (
            "tests/test_mutation38_task_nonmovement_lock_scope.py::test_n"
            "onmovement_duty_completion_does_not_lock_a_dams_recovery_kid"
        ),
    ),
    ("app/api/tasks.py", "_lock_farm_for_recurring_transition"): (
        (
            "tests/test_mutation38_task_recurrence_queue_contracts.py::te"
            "st_actual_recurring_task_identifier_gates_preserve_queue_acq"
            "uisition_order[first]"
        ),
        (
            "tests/test_mutation38_task_recurrence_queue_contracts.py::te"
            "st_actual_recurring_task_identifier_gates_preserve_queue_acq"
            "uisition_order[last]"
        ),
        (
            "tests/test_mutation38_task_recurrence_queue_contracts.py::te"
            "st_actual_recurring_task_identifier_gates_preserve_queue_acq"
            "uisition_order[retained-zero]"
        ),
    ),
    ("app/api/tasks.py", "create_task"): (
        (
            "tests/test_mutation38_manual_task_assignment_contracts.py::t"
            "est_invalid_animal_assignment_is_400_atomic_and_failed_key_c"
            "an_retry"
        ),
        (
            "tests/test_mutation38_manual_task_assignment_contracts.py::t"
            "est_manual_assignment_admits_real_constraint_valid_int4_ceil"
            "ing_identity"
        ),
        (
            "tests/test_mutation38_manual_task_assignment_contracts.py::t"
            "est_manual_task_admission_coexists_with_independent_shared_r"
            "eference_reader"
        ),
    ),
    ("app/api/tasks.py", "get_task"): (
        (
            "tests/test_mutation38_task_detail_identifier_contracts.py::t"
            "est_actual_retained_task_deep_link_preserves_the_public_iden"
            "tifier_band[largest-int4]"
        ),
        (
            "tests/test_mutation38_task_detail_identifier_contracts.py::t"
            "est_actual_retained_task_deep_link_preserves_the_public_iden"
            "tifier_band[retained-zero]"
        ),
    ),
    ("app/api/tasks.py", "list_tasks"): (
        (
            "tests/test_mutation38_task_count_scope_contracts.py::test_on"
            "e_real_linked_animal_contributes_to_its_own_pending_tab_coun"
            "t"
        ),
        (
            "tests/test_mutation38_task_count_scope_contracts.py::test_ve"
            "rifier_counts_another_roles_actual_review_duty_without_histo"
            "ry_scope"
        ),
        (
            "tests/test_mutation38_task_tab_contracts.py::test_task_tabs_"
            "expose_the_published_default_page_metadata"
        ),
        (
            "tests/test_mutation38_task_tab_contracts.py::test_task_tabs_"
            "preserve_actual_pending_review_and_completed_cohorts"
        ),
        (
            "tests/test_mutation38_task_worker_review_count.py::test_work"
            "er_without_verification_permission_counts_its_completed_revi"
            "ew_duty"
        ),
    ),
    ("app/api/tasks.py", "mutate"): (
        (
            "tests/test_mutation38_task_completion_skip_race.py::test_ski"
            "p_queued_behind_a_real_public_completion_preserves_done_attr"
            "ibution"
        ),
        (
            "tests/test_mutation38_task_heat_watch_skip.py::test_optional"
            "_generated_heat_watch_can_be_skipped_while_the_scan_stays_op"
            "en"
        ),
        (
            "tests/test_mutation38_task_skip_completion_race.py::test_com"
            "pletion_queued_behind_a_real_public_skip_preserves_skipped_a"
            "ttribution"
        ),
    ),
    ("app/api/tasks.py", "reject"): (
        (
            "tests/test_mutation38_task_review_contracts.py::test_conflic"
            "ting_public_reviews_recheck_the_committed_task_state"
        ),
    ),
    ("app/api/tasks.py", "verify"): (
        (
            "tests/test_mutation38_task_review_contracts.py::test_conflic"
            "ting_public_reviews_recheck_the_committed_task_state"
        ),
        (
            "tests/test_mutation38_task_review_contracts.py::test_reviewe"
            "d_linked_recurrence_continues_only_while_its_animal_is_activ"
            "e"
        ),
    ),
    ("app/models/animals.py", "_derive_history_farm_id"): (
        (
            "tests/test_mutation38_livestock_model_bucket_history.py::tes"
            "t_history_derivation_continues_after_a_legitimate_unresolved"
            "_pending_identity"
        ),
    ),
    ("app/models/animals.py", "days_in_current_bucket_on"): (
        (
            "tests/test_mutation38_livestock_model_bucket_history.py::tes"
            "t_elapsed_bucket_days_accept_a_real_business_effective_date_"
            "and_clamp_prearrival"
        ),
    ),
    ("app/models/animals.py", "display_name"): (
        (
            "tests/test_mutation38_livestock_model_positive_contracts.py:"
            ":test_valid_animal_display_name_is_readable"
        ),
    ),
    ("app/models/animals.py", "is_breeding_eligible_on"): (
        (
            "tests/test_mutation38_livestock_doe_model_bucket_gate.py::te"
            "st_doe_eligibility_requires_a_released_cohort_even_at_the_cl"
            "inical_floors"
        ),
    ),
    ("app/models/animals.py", "last_bucket_move"): (
        (
            "tests/test_mutation38_livestock_model_bucket_history.py::tes"
            "t_last_move_prefers_an_assigned_identity_until_a_pending_mov"
            "e_is_flushed"
        ),
        (
            "tests/test_mutation38_livestock_model_bucket_history.py::tes"
            "t_last_move_same_audit_instant_uses_the_newer_persisted_iden"
            "tity"
        ),
    ),
    ("app/models/animals.py", "latest_weight"): (
        (
            "tests/test_mutation38_livestock_model_weight_order.py::test_"
            "latest_weight_keeps_assigned_identity_ahead_of_an_unflushed_"
            "same_day_reading"
        ),
        (
            "tests/test_mutation38_livestock_model_weight_order.py::test_"
            "latest_weight_same_day_uses_persisted_entry_order_not_collec"
            "tion_order"
        ),
    ),
    ("app/models/animals.py", "latest_weight_kg"): (
        (
            "tests/test_mutation38_livestock_model_positive_contracts.py:"
            ":test_latest_weight_returns_measurement_or_birth_weight_with"
            "out_error"
        ),
    ),
    ("app/models/animals.py", "latest_weight_kg_on"): (
        (
            "tests/test_mutation38_livestock_model_positive_contracts.py:"
            ":test_as_of_weight_orders_pending_and_persisted_same_day_rec"
            "ords_without_error"
        ),
        (
            "tests/test_mutation38_livestock_model_positive_contracts.py:"
            ":test_unknown_dob_keeps_recorded_birth_weight_readable"
        ),
    ),
    ("app/models/feed_rules.py", "creep_daily_kg"): (
        (
            "tests/test_mutation38_livestock_feed_native_context.py::test"
            "_native_creep_ration_retains_the_literal_band_allowance"
        ),
    ),
    ("app/models/feed_rules.py", "recipe_age_days"): (
        (
            "tests/test_mutation38_livestock_feed_native_context.py::test"
            "_native_known_age_is_a_calendar_difference_including_the_lea"
            "p_day"
        ),
    ),
    ("app/models/feed_rules.py", "recipe_for_context"): (
        (
            "tests/test_mutation38_livestock_feed_native_context.py::test"
            "_native_recovery_ration_requires_explicit_dependent_kid_cont"
            "ext"
        ),
    ),
    ("app/models/helpers.py", "conception_rate"): (
        (
            "tests/test_mutation38_livestock_model_positive_contracts.py:"
            ":test_valid_breeding_history_returns_conception_rate_without"
            "_error"
        ),
    ),
    ("app/models/tasks.py", "awaiting_verification_clause"): (
        (
            "tests/test_mutation38_task_tab_contracts.py::test_task_tabs_"
            "preserve_actual_pending_review_and_completed_cohorts"
        ),
    ),
    ("app/schemas/finance.py", "_renewal_horizon_bounds"): (
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_i"
            "nsurance_native_input_preserves_the_independent_five_year_ho"
            "rizon[same-day-cover]"
        ),
    ),
    ("app/services/animals.py", "_skip_locked_pending_tasks"): (
        (
            "tests/test_mutation38_livestock_cleanup_service_contracts.py"
            "::test_cleanup_retains_exactly_255_characters_of_a_long_nati"
            "ve_reason"
        ),
    ),
    ("app/services/animals.py", "bucket_transition_error"): (
        (
            "tests/test_mutation38_livestock_buck_age_transition.py::test"
            "_native_sire_housing_accepts_twelve_month_boundary"
        ),
        (
            "tests/test_mutation38_livestock_transition_native_contracts."
            "py::test_native_breeding_guard_accepts_the_literal_doe_and_s"
            "ire_weight_floors"
        ),
        (
            "tests/test_mutation38_livestock_transition_native_contracts."
            "py::test_native_guard_defaults_require_explicit_reclassifica"
            "tion_during_each_hold"
        ),
        (
            "tests/test_mutation38_livestock_transition_native_contracts."
            "py::test_same_day_rest_guard_reports_zero_completed_days_and"
            "_exact_earliest_date"
        ),
    ),
    ("app/services/animals.py", "generate_unique_tag"): (
        (
            "tests/test_mutation38_livestock_service_tag_budget.py::test_"
            "native_tag_allocator_accepts_tenth_candidate_and_stops_after"
            "_ten_collisions"
        ),
    ),
    ("app/services/animals.py", "move_animal"): (
        (
            "tests/test_mutation38_livestock_move_hold_contract.py::test_"
            "a_valid_movement_hold_completes_without_relocating_or_record"
            "ing_history"
        ),
        (
            "tests/test_mutation38_livestock_transition_native_contracts."
            "py::test_native_move_keeps_held_animals_unchanged_without_ex"
            "plicit_opt_in"
        ),
        (
            "tests/test_mutation38_livestock_transition_native_contracts."
            "py::test_native_move_retains_the_supplied_audit_reason_in_pe"
            "nding_history"
        ),
    ),
    ("app/services/animals.py", "require_bucket_transition"): (
        (
            "tests/test_mutation38_livestock_transition_native_contracts."
            "py::test_native_required_transition_keeps_the_default_hold_f"
            "ence"
        ),
    ),
    ("app/services/animals.py", "skip_inactive_animal_tasks_batch"): (
        (
            "tests/test_mutation38_livestock_inactive_cleanup_contract.py"
            "::test_inactive_cleanup_enforces_its_literal_batch_bounds_an"
            "d_preserves_valid_work"
        ),
        (
            "tests/test_mutation38_livestock_inactive_cleanup_contract.py"
            "::test_inactive_cleanup_skips_a_real_locked_task_and_converg"
            "es_unlocked_other_farm_work"
        ),
    ),
    ("app/services/animals.py", "skip_pending_tasks_for_animal"): (
        (
            "tests/test_inactive_animal_task_cleanup.py::test_high_cardin"
            "ality_retirement_is_hidden_then_converges_in_batches"
        ),
        (
            "tests/test_mutation38_livestock_cleanup_service_contracts.py"
            "::test_animal_cleanup_skips_a_contended_duty_and_processes_u"
            "nlocked_work"
        ),
        (
            "tests/test_mutation38_livestock_cleanup_service_contracts.py"
            "::test_cleanup_accepts_inclusive_literal_bounds_and_rejects_"
            "outside"
        ),
    ),
    ("app/services/animals.py", "skip_pending_tasks_for_empty_batch"): (
        (
            "tests/test_mutation38_livestock_cleanup_service_contracts.py"
            "::test_cleanup_accepts_inclusive_literal_bounds_and_rejects_"
            "outside"
        ),
        (
            "tests/test_mutation38_livestock_cleanup_service_contracts.py"
            "::test_empty_batch_cleanup_preserves_the_default_500_row_bud"
            "get"
        ),
        (
            "tests/test_mutation38_livestock_cleanup_service_contracts.py"
            "::test_foreign_batch_reports_zero_without_touching_its_dutie"
            "s"
        ),
        (
            "tests/test_mutation38_livestock_cleanup_service_contracts.py"
            "::test_nonempty_two_animal_batch_reports_zero_and_keeps_duti"
            "es_pending"
        ),
    ),
    ("app/services/breeding.py", "_buck_open_service_count"): (
        (
            "tests/test_mutation38_livestock_buck_open_service_count.py::"
            "test_sire_open_count_matches_real_pending_and_undelivered_co"
            "horts"
        ),
    ),
    ("app/services/breeding.py", "_fit_generated_title"): (
        (
            "tests/test_mutation38_livestock_generated_title_room.py::tes"
            "t_native_title_fit_preserves_operational_text_when_tag_room_"
            "runs_out"
        ),
    ),
    ("app/services/breeding.py", "_latest_doe_reproductive_boundary"): (
        (
            "tests/test_mutation38_livestock_reproductive_boundary_facts."
            "py::test_native_reproductive_boundary_includes_real_kidding_"
            "and_preserves_tenant_scope"
        ),
    ),
    ("app/services/breeding.py", "_latest_kidding_date"): (
        (
            "tests/test_mutation38_livestock_native_breeding_guards.py::t"
            "est_native_service_preserves_the_actual_postpartum_waiting_f"
            "loor"
        ),
    ),
    ("app/services/breeding.py", "_latest_weight_as_of"): (
        (
            "tests/test_mutation38_livestock_breeding_birth_weight_dates."
            "py::test_native_sql_weight_fact_preserves_literal_birth_date"
            "_availability"
        ),
    ),
    ("app/services/breeding.py", "breeding_candidate_page"): (
        (
            "tests/test_mutation38_livestock_breeding_candidate_dates.py:"
            ":test_native_candidate_queries_keep_historical_weight_separa"
            "te_from_current_default"
        ),
    ),
    ("app/services/breeding.py", "create_breeding_record"): (
        (
            "tests/test_mutation38_breeding_rebreed_completion_race.py::t"
            "est_new_service_preserves_a_real_concurrently_committed_rebr"
            "eed_completion"
        ),
        (
            "tests/test_mutation38_livestock_breeding_chronology_equality"
            ".py::test_historical_arrival_day_service_is_valid_for_eligib"
            "le_imported_adults"
        ),
        (
            "tests/test_mutation38_livestock_breeding_chronology_equality"
            ".py::test_native_service_on_birth_day_reports_eligibility_no"
            "t_prior_birth_chronology"
        ),
        (
            "tests/test_mutation38_livestock_breeding_metadata.py::test_n"
            "ative_natural_cover_discards_unused_semen_metadata"
        ),
        (
            "tests/test_mutation38_livestock_breeding_metadata.py::test_p"
            "ublic_heat_watch_localization_uses_the_actual_future_due_dat"
            "e"
        ),
        (
            "tests/test_mutation38_livestock_cull_override_audit.py::test"
            "_owner_breeding_audit_claims_only_a_real_cull_candidate_over"
            "ride"
        ),
        (
            "tests/test_mutation38_livestock_native_breeding_guards.py::t"
            "est_native_service_preserves_the_actual_postpartum_waiting_f"
            "loor"
        ),
        (
            "tests/test_mutation38_livestock_native_breeding_guards.py::t"
            "est_omitting_native_owner_authority_cannot_override_a_real_c"
            "ull_candidate"
        ),
    ),
    ("app/services/breeding.py", "derived_heat_cycle_number"): (
        (
            "tests/test_mutation38_livestock_service_calendar_caps.py::te"
            "st_native_recorded_failed_history_saturates_at_schema_cycle9"
            "9"
        ),
    ),
    ("app/services/breeding.py", "doe_has_open_breeding"): (
        (
            "tests/test_mutation38_livestock_restored_open_pregnancy.py::"
            "test_restored_confirmed_provenance_still_means_an_open_pregn"
            "ancy"
        ),
    ),
    ("app/services/breeding.py", "is_breeding_candidate"): (
        (
            "tests/test_mutation38_livestock_breeding_predicate_weight_fl"
            "oors.py::test_canonical_service_predicates_preserve_inclusiv"
            "e_literal_weight_floors"
        ),
    ),
    ("app/services/breeding.py", "is_buck_breeding_candidate"): (
        (
            "tests/test_mutation38_livestock_breeding_predicate_age_floor"
            "s.py::test_canonical_service_predicates_accept_exact_twelve_"
            "month_calendar_anniversary"
        ),
        (
            "tests/test_mutation38_livestock_breeding_predicate_weight_fl"
            "oors.py::test_canonical_service_predicates_preserve_inclusiv"
            "e_literal_weight_floors"
        ),
    ),
    ("app/services/breeding.py", "mark_aborted"): (
        (
            "tests/test_mutation38_livestock_abort_task_race.py::test_rec"
            "orded_loss_preserves_committed_birthing_kit_completion"
        ),
        (
            "tests/test_mutation38_livestock_migrated_pregnancy_exit.py::"
            "test_migrated_unknown_scan_date_allows_administrative_exit_o"
            "n_service_date"
        ),
        (
            "tests/test_mutation38_livestock_pregnancy_loss_limits.py::te"
            "st_native_pregnancy_loss_preserves_literal_four_thousand_cha"
            "racter_notes_limit"
        ),
        (
            "tests/test_mutation38_livestock_pregnancy_loss_limits.py::te"
            "st_public_pregnancy_loss_preserves_inclusive_two_hundred_day"
            "_sanity_ceiling"
        ),
    ),
    ("app/services/breeding.py", "mark_unassessed"): (
        (
            "tests/test_mutation38_livestock_unassessed_task_audit.py::te"
            "st_public_unassessed_closure_attributes_cancelled_pregnancy_"
            "work_to_its_actor"
        ),
        (
            "tests/test_mutation38_livestock_unassessed_task_audit.py::te"
            "st_unassessed_close_preserves_a_committing_native_heat_watch"
            "_completion"
        ),
    ),
    ("app/services/breeding.py", "record_ultrasound_result"): (
        (
            "tests/test_mutation38_breeding_schema_boundaries.py::test_br"
            "eeding_accepts_upper_wire_cycle_and_a_supported_quadruplet_s"
            "can"
        ),
        (
            "tests/test_mutation38_livestock_pregnancy_duty_calendar.py::"
            "test_confirmed_pregnancy_publishes_consistent_dates_and_loca"
            "lized_duties"
        ),
        (
            "tests/test_mutation38_ultrasound_legacy_skip_race.py::test_s"
            "can_preserves_a_real_concurrent_skip_of_a_retained_unlinked_"
            "appointment"
        ),
        (
            "tests/test_mutation38_ultrasound_task_races.py::test_positiv"
            "e_scan_preserves_a_real_concurrent_heat_watch_completion"
        ),
    ),
    ("app/services/cadence.py", "_ensure_buck_rotations"): (
        (
            "tests/test_mutation38_cadence_cached_scan_continuation.py::t"
            "est_cached_unknown_buck_age_does_not_starve_the_next_real_ro"
            "tation"
        ),
        (
            "tests/test_mutation38_cadence_native_scan_conservation.py::t"
            "est_buck_rotation_scan_conserves_its_documented_first_five_h"
            "undred_actual_bucks"
        ),
        (
            "tests/test_mutation38_workflow_buck_rotation_window_formatte"
            "d.py::test_buck_rotation_history_includes_the_365th_day_in_e"
            "very_status"
        ),
        (
            "tests/test_mutation38_workflow_cadence_transaction.py::test_"
            "noop_cadence_does_not_commit_a_callers_staged_farm_edit"
        ),
    ),
    ("app/services/cadence.py", "_ensure_calendar_rounds"): (
        (
            "tests/test_mutation38_cadence_calendar_native_contracts.py::"
            "test_backfill_admits_a_round_whose_month_end_is_exactly_the_"
            "introduction_floor"
        ),
        (
            "tests/test_mutation38_cadence_calendar_native_contracts.py::"
            "test_calendar_dedupe_keeps_later_series_and_snapshots_the_ac"
            "tual_herd"
        ),
        (
            "tests/test_mutation38_workflow_cadence_transaction.py::test_"
            "noop_cadence_does_not_commit_a_callers_staged_farm_edit"
        ),
    ),
    ("app/services/cadence.py", "_ensure_daily_feed_routine"): (
        (
            "tests/test_mutation38_cadence_creation_reports.py::test_new_"
            "cadence_work_survives_the_native_callers_conditional_commit"
        ),
        (
            "tests/test_mutation38_workflow_cadence_transaction.py::test_"
            "noop_cadence_does_not_commit_a_callers_staged_farm_edit"
        ),
    ),
    ("app/services/cadence.py", "_ensure_daily_water_check"): (
        (
            "tests/test_mutation38_cadence_creation_reports.py::test_new_"
            "cadence_work_survives_the_native_callers_conditional_commit"
        ),
        (
            "tests/test_mutation38_workflow_cadence_transaction.py::test_"
            "noop_cadence_does_not_commit_a_callers_staged_farm_edit"
        ),
    ),
    ("app/services/cadence.py", "_ensure_feed_reorders"): (
        (
            "tests/test_mutation38_cadence_cached_scan_continuation.py::t"
            "est_cached_unconfigured_ingredient_does_not_starve_the_next_"
            "real_reorder"
        ),
        (
            "tests/test_mutation38_cadence_native_scan_conservation.py::t"
            "est_feed_reorder_scan_conserves_the_documented_first_hundred"
            "_ingredient_duties"
        ),
        (
            "tests/test_mutation38_cadence_native_scan_conservation.py::t"
            "est_reorder_threshold_is_strict_and_existing_identity_does_n"
            "ot_starve_later_stock"
        ),
        (
            "tests/test_mutation38_workflow_cadence_transaction.py::test_"
            "noop_cadence_does_not_commit_a_callers_staged_farm_edit"
        ),
    ),
    ("app/services/cadence.py", "_ensure_interval_rounds"): (
        (
            "tests/test_mutation38_cadence_interval_expiry.py::test_inter"
            "val_round_expires_only_after_its_last_inclusive_day"
        ),
        (
            "tests/test_mutation38_workflow_cadence_forward_window.py::te"
            "st_interval_forward_window_ends_on_the_thirtieth_day"
        ),
        (
            "tests/test_mutation38_workflow_cadence_transaction.py::test_"
            "noop_cadence_does_not_commit_a_callers_staged_farm_edit"
        ),
    ),
    ("app/services/cadence.py", "_ensure_ppr_round"): (
        (
            "tests/test_mutation38_cadence_creation_reports.py::test_annu"
            "al_ppr_suppression_resolves_the_newest_of_two_actual_prior_r"
            "ounds"
        ),
        (
            "tests/test_mutation38_cadence_creation_reports.py::test_new_"
            "cadence_work_survives_the_native_callers_conditional_commit"
        ),
        (
            "tests/test_mutation38_cadence_creation_reports.py::test_ppr_"
            "operator_schedule_at_day_thirty_suppresses_a_new_current_rou"
            "nd"
        ),
        (
            "tests/test_mutation38_workflow_cadence_transaction.py::test_"
            "noop_cadence_does_not_commit_a_callers_staged_farm_edit"
        ),
    ),
    ("app/services/cadence.py", "_farm_earliest_introduction"): (
        (
            "tests/test_mutation38_cadence_native_page_contracts.py::test"
            "_native_earliest_introduction_uses_only_its_real_purchase_te"
            "nant"
        ),
    ),
    ("app/services/cadence.py", "_farm_has_active_animals"): (
        (
            "tests/test_mutation38_cadence_native_page_contracts.py::test"
            "_native_cadence_existence_probes_accept_two_real_matching_ro"
            "ws"
        ),
    ),
    ("app/services/cadence.py", "_latest_occurrence"): (
        (
            "tests/test_mutation38_cadence_calendar_native_contracts.py::"
            "test_latest_calendar_occurrence_is_the_actual_series_month_s"
            "tart"
        ),
    ),
    ("app/services/cadence.py", "_month_bounds"): (
        (
            "tests/test_mutation38_cadence_calendar_native_contracts.py::"
            "test_calendar_month_bounds_cover_the_whole_actual_month"
        ),
    ),
    ("app/services/cadence.py", "_task_exists"): (
        (
            "tests/test_mutation38_cadence_native_page_contracts.py::test"
            "_native_cadence_existence_probes_accept_two_real_matching_ro"
            "ws"
        ),
    ),
    ("app/services/cadence.py", "ensure_cadence_farm_batch"): (
        (
            "tests/test_mutation38_cadence_native_page_contracts.py::test"
            "_native_cadence_page_admits_exactly_the_documented_size_rang"
            "e"
        ),
        (
            "tests/test_mutation38_cadence_native_page_contracts.py::test"
            "_native_page_refreshes_a_real_cached_tenants_committed_busin"
            "ess_timezone"
        ),
        (
            "tests/test_mutation38_cadence_retained_page_continuation.py:"
            ":test_a_really_retained_missing_farm_does_not_starve_the_nex"
            "t_keyset_tenant"
        ),
    ),
    ("app/services/cadence.py", "ensure_cadence_tasks"): (
        (
            "tests/test_mutation38_workflow_cadence_transaction.py::test_"
            "noop_cadence_does_not_commit_a_callers_staged_farm_edit"
        ),
    ),
    ("app/services/dashboard.py", "bakrid_hold_advisory"): (
        (
            "tests/test_mutation38_dashboard_native_contract_v4.py::test_"
            "native_month_end_births_do_not_break_the_actual_calendar_adv"
            "isory"
        ),
        (
            "tests/test_mutation38_dashboard_single_calendar_native_contr"
            "act_v1.py::test_native_single_male_finishes_on_the_ninth_mon"
            "th_hold_window_edge"
        ),
    ),
    ("app/services/dashboard.py", "ready_to_move_suggestions"): (
        (
            "tests/test_mutation38_dashboard_breeding_boundaries_native_c"
            "ontract_v1.py::test_native_current_pregnancy_is_not_closed_b"
            "y_another_does_recorded_birth"
        ),
        (
            "tests/test_mutation38_dashboard_breeding_boundaries_native_c"
            "ontract_v1.py::test_native_foundation_maturity_suggests_the_"
            "doe_and_not_the_male"
        ),
        (
            "tests/test_mutation38_dashboard_breeding_boundaries_native_c"
            "ontract_v2.py::test_native_rest_doe_is_mature_on_the_exact_t"
            "welfth_month_date"
        ),
        (
            "tests/test_mutation38_dashboard_native_contract_v4.py::test_"
            "native_readiness_uses_dated_weight_rest_history_and_current_"
            "safety_facts"
        ),
        (
            "tests/test_mutation38_dashboard_retained_history_native_cont"
            "ract_v1.py::test_latest_retained_positive_service_remains_re"
            "adable_with_an_old_unlinked_birth_history"
        ),
        (
            "tests/test_mutation38_dashboard_retained_history_native_cont"
            "ract_v1.py::test_two_real_lifecycle_moves_keep_the_latest_re"
            "st_program_readable"
        ),
    ),
    ("app/services/feeding.py", "_allocate_recipe_grams"): (
        (
            "tests/test_mutation38_livestock_feed_native_precision.py::te"
            "st_half_gram_floor_and_exact_two_ingredient_batch_admission"
        ),
    ),
    ("app/services/feeding.py", "_positive_kg"): (
        (
            "tests/test_mutation38_livestock_feed_native_precision.py::te"
            "st_half_gram_floor_and_exact_two_ingredient_batch_admission"
        ),
        (
            "tests/test_mutation38_livestock_feed_native_precision.py::te"
            "st_invalid_native_stock_quantity_reports_a_value_error"
        ),
    ),
    ("app/services/feeding.py", "_restock_money"): (
        (
            "tests/test_mutation38_livestock_feed_native_precision.py::te"
            "st_native_restock_preserves_zero_price_and_rejects_unreprese"
            "ntable_positive_money"
        ),
    ),
    ("app/services/feeding.py", "_shift_quantities"): (
        (
            "tests/test_mutation38_livestock_feed_native_precision.py::te"
            "st_small_shift_ration_uses_actual_largest_residuals_and_enum"
            "_ties"
        ),
    ),
    ("app/services/feeding.py", "feeding_plan"): (
        (
            "tests/test_mutation38_feeding_configured_storage_order.py::t"
            "est_breeding_display_order_is_independent_of_configured_data"
            "base_collation"
        ),
        (
            "tests/test_mutation38_feeding_creep_conservation.py::test_da"
            "y_sixty_dependent_creep_row_is_present_and_conserves_the_rea"
            "l_cohort"
        ),
        (
            "tests/test_mutation38_feeding_native_provenance_order.py::te"
            "st_creep_requires_its_actual_dam_to_be_in_recovery"
        ),
        (
            "tests/test_mutation38_feeding_sql_cohorts.py::test_an_alread"
            "y_weaned_day_sixty_grower_keeps_its_actual_weight_basis"
        ),
        (
            "tests/test_mutation38_feeding_sql_cohorts.py::test_breeding_"
            "plan_orders_female_then_male_and_supplements_only_bucks"
        ),
        (
            "tests/test_mutation38_feeding_sql_cohorts.py::test_creep_inc"
            "ludes_day_sixty_and_requires_the_actual_active_recovery_dam"
        ),
        (
            "tests/test_mutation38_feeding_sql_cohorts.py::test_historica"
            "l_plan_preserves_one_head_and_the_latest_as_of_scale_reading"
        ),
        (
            "tests/test_mutation38_feeding_sql_cohorts.py::test_plan_orde"
            "rs_equal_native_reference_priorities_with_a_missing_definiti"
            "on"
        ),
        (
            "tests/test_mutation38_feeding_sql_cohorts.py::test_quarantin"
            "e_plan_switches_on_the_literal_third_calendar_day"
        ),
    ),
    ("app/services/feeding.py", "mix_feed_batch"): (
        (
            "tests/test_mutation38_livestock_feed_pending_recipe_validati"
            "on.py::test_native_mix_reports_an_invalid_pending_ingredient"
            "_before_stock_or_ledger_effects"
        ),
        (
            "tests/test_mutation38_livestock_feed_stock_conservation.py::"
            "test_absent_unreferenced_recipe_ingredient_cannot_credit_fin"
            "ished_feed"
        ),
        (
            "tests/test_mutation38_livestock_feed_stock_conservation.py::"
            "test_native_mix_accepts_a_single_six_decimal_reference_quant"
            "um_at_mass_tolerance"
        ),
        (
            "tests/test_mutation38_livestock_feed_stock_conservation.py::"
            "test_two_fractional_real_mixes_conserve_each_ingredient_and_"
            "finished_feed"
        ),
    ),
    ("app/services/feeding.py", "recipe_for_animal"): (
        (
            "tests/test_mutation38_livestock_feed_native_context.py::test"
            "_native_recipe_respects_the_supplied_as_of_date"
        ),
    ),
    ("app/services/feeding.py", "record_dispensing"): (
        (
            "tests/test_mutation38_livestock_feed_stock_conservation.py::"
            "test_absent_finished_stock_reports_zero_available_and_create"
            "s_no_dispensing_record"
        ),
        (
            "tests/test_mutation38_livestock_feed_stock_conservation.py::"
            "test_insufficient_existing_stock_is_rejected_without_negativ"
            "e_balance_or_record"
        ),
        (
            "tests/test_mutation38_livestock_feed_stock_conservation.py::"
            "test_missing_unreferenced_raw_row_reports_zero_and_cannot_cr"
            "eate_feed_from_nothing"
        ),
    ),
    ("app/services/finance.py", "_book_premium"): (
        (
            "tests/test_mutation38_livestock_insurance_calendar_edges.py:"
            ":test_real_current_day_claim_admits_both_coverage_endpoints_"
            "and_keeps_premium_facts"
        ),
    ),
    ("app/services/finance.py", "_lock_new_policy_animal_active"): (
        (
            "tests/test_mutation38_native_insurance_lock_contracts.py::te"
            "st_new_policy_guard_rejects_a_cached_animal_after_an_actual_"
            "public_sale"
        ),
    ),
    ("app/services/finance.py", "_require_linked_animal_active"): (
        (
            "tests/test_mutation38_livestock_finance_native_boundaries.py"
            "::test_native_covered_animal_gate_accepts_active_and_rejects"
            "_real_sold_and_foreign_cover"
        ),
    ),
    ("app/services/finance.py", "claim_insurance_policy"): (
        (
            "tests/test_mutation38_livestock_insurance_calendar_edges.py:"
            ":test_real_current_day_claim_admits_both_coverage_endpoints_"
            "and_keeps_premium_facts"
        ),
    ),
    ("app/services/finance.py", "create_insurance_policy"): (
        (
            "tests/test_mutation38_livestock_insurance_calendar_edges.py:"
            ":test_real_current_day_claim_admits_both_coverage_endpoints_"
            "and_keeps_premium_facts"
        ),
    ),
    ("app/services/finance.py", "lifetime_pnl"): (
        (
            "tests/test_mutation38_native_finance_aggregates.py::test_bat"
            "ch_allocated_purchase_cost_is_not_another_goats_ledger"
        ),
    ),
    ("app/services/finance.py", "lock_insurance_policy"): (
        (
            "tests/test_mutation38_native_insurance_lock_contracts.py::te"
            "st_native_policy_lock_refreshes_cached_cover_after_an_actual"
            "_public_sale"
        ),
        (
            "tests/test_mutation38_native_insurance_lock_contracts.py::te"
            "st_native_policy_lookup_locks_and_returns_the_actual_same_fa"
            "rm_policy"
        ),
    ),
    ("app/services/finance.py", "monthly_pnl"): (
        (
            "tests/test_mutation38_native_finance_aggregates.py::test_nat"
            "ive_monthly_summary_uses_exact_half_open_calendar_windows"
        ),
    ),
    ("app/services/finance.py", "mortality_memo"): (
        (
            "tests/test_mutation38_native_mortality_memo.py::test_mortali"
            "ty_window_retains_first_day_and_excludes_next_month_facts"
        ),
        (
            "tests/test_mutation38_native_mortality_memo.py::test_one_rup"
            "ee_realized_sale_still_values_a_positive_fractional_weight"
        ),
    ),
    ("app/services/finance.py", "renew_insurance_policy"): (
        (
            "tests/test_mutation38_livestock_insurance_calendar_edges.py:"
            ":test_real_renewal_accepts_literal1830_days_records_premium_"
            "and_replays_without_rebooking"
        ),
    ),
    ("app/services/health.py", "_has_alias"): (
        (
            "tests/test_mutation38_health_alias_boundary_contracts.py::te"
            "st_blank_alias_does_not_hide_a_later_complete_alias"
        ),
        (
            "tests/test_mutation38_health_programme_vocabulary.py::test_n"
            "ative_health_aliases_require_the_declared_complete_nonblank_"
            "words"
        ),
    ),
    ("app/services/health.py", "canonical_target_for_task"): (
        (
            "tests/test_mutation38_livestock_health_component_audit.py::t"
            "est_single_component_duty_never_claims_or_accepts_the_other_"
            "vaccine"
        ),
    ),
    ("app/services/health.py", "inferred_schedule_template"): (
        (
            "tests/test_mutation38_health_schedule_boundaries.py::test_se"
            "eded_first_dose_only_programme_participates_in_unique_native"
            "_inference"
        ),
    ),
    ("app/services/health.py", "place_movement_restriction"): (
        (
            "tests/test_mutation38_livestock_health_component_audit.py::t"
            "est_native_restriction_placement_preserves_the_supplied_audi"
            "t_instant"
        ),
    ),
    ("app/services/health.py", "protocol_phrase_of"): (
        (
            "tests/test_mutation38_health_alias_boundary_contracts.py::te"
            "st_native_title_prefix_preserves_the_entire_plain_protocol_p"
            "hrase"
        ),
    ),
    ("app/services/health.py", "target_matches_task"): (
        (
            "tests/test_mutation38_livestock_health_component_audit.py::t"
            "est_single_component_duty_never_claims_or_accepts_the_other_"
            "vaccine"
        ),
    ),
    ("app/services/health.py", "template_names_for_task"): (
        (
            "tests/test_mutation38_health_alias_boundary_contracts.py::te"
            "st_combined_round_requires_both_declared_component_aliases"
        ),
    ),
    ("app/services/health.py", "vaccination_schedule_for_animal"): (
        (
            "tests/test_mutation38_health_schedule_boundaries.py::test_a_"
            "recorded_primary_dose_is_not_overdue_on_its_actual_booster_d"
            "ay"
        ),
        (
            "tests/test_mutation38_health_schedule_boundaries.py::test_sc"
            "hedule_uses_its_farm_calendar_on_the_first_due_anniversary"
        ),
        (
            "tests/test_mutation38_health_schedule_row_budget.py::test_fo"
            "ur_actual_linked_doses_preserve_anchors_with_at_most_three_p"
            "robe_rows"
        ),
    ),
    ("app/services/health_rounds.py", "ensure_round_snapshot"): (
        (
            "tests/test_mutation38_native_health_round_contracts.py::test"
            "_native_pending_snapshot_retains_exact_farm_members_and_init"
            "ial_provenance"
        ),
        (
            "tests/test_mutation38_native_health_round_contracts.py::test"
            "_native_snapshot_rejects_a_real_skipped_duty_without_backfil"
            "ling"
        ),
    ),
    ("app/services/health_rounds.py", "is_herd_round"): (
        (
            "tests/test_mutation38_native_health_round_contracts.py::test"
            "_native_pending_snapshot_retains_exact_farm_members_and_init"
            "ial_provenance"
        ),
    ),
    ("app/services/health_rounds.py", "recorded_components"): (
        (
            "tests/test_mutation38_native_health_round_contracts.py::test"
            "_native_blank_evidence_names_one_actual_required_component"
        ),
    ),
    ("app/services/health_rounds.py", "require_round_targets"): (
        (
            "tests/test_mutation38_native_health_round_contracts.py::test"
            "_native_round_admission_and_coverage_use_the_exact_exclusion"
            "_cohort"
        ),
    ),
    ("app/services/health_rounds.py", "round_counts"): (
        (
            "tests/test_mutation38_native_health_round_contracts.py::test"
            "_native_round_admission_and_coverage_use_the_exact_exclusion"
            "_cohort"
        ),
    ),
    ("app/services/health_rounds.py", "round_is_complete"): (
        (
            "tests/test_mutation38_native_health_round_contracts.py::test"
            "_native_empty_farm_is_not_completed_treatment_evidence"
        ),
    ),
    ("app/services/kidding.py", "record_kidding"): (
        (
            "tests/test_mutation38_livestock_kidding_neonatal_notes.py::t"
            "est_public_birth_keeps_neonatal_mortality_notes_coherent_wit"
            "h_child_status"
        ),
        (
            "tests/test_mutation38_livestock_kidding_tag_admission.py::te"
            "st_native_kidding_retains_omitted_clinical_default_and_predi"
            "ctable_tag_rejection"
        ),
        (
            "tests/test_mutation38_livestock_kidding_tag_admission.py::te"
            "st_public_kid_tag_fallback_retains_its_four_attempt_budget"
        ),
        (
            "tests/test_mutation38_livestock_kidding_tag_admission.py::te"
            "st_public_litter_retries_an_already_assigned_random_fallback"
        ),
        (
            "tests/test_mutation38_livestock_kidding_task_ownership.py::t"
            "est_kidding_preserves_a_committing_native_birthing_kit_compl"
            "etion"
        ),
        (
            "tests/test_mutation38_livestock_postpartum_duty_calendar.py:"
            ":test_public_postpartum_duties_follow_real_litter_outcomes_a"
            "nd_localized_dates"
        ),
    ),
    ("app/services/kidding.py", "replan_dam_after_last_kid_death"): (
        (
            "tests/test_mutation38_kid_death_replan_contracts.py::test_a_"
            "dam_without_her_own_birth_entry_has_no_parent_plan_to_rewrit"
            "e"
        ),
        (
            "tests/test_mutation38_kid_death_replan_contracts.py::test_a_"
            "genuinely_weaned_child_restored_to_recovery_is_not_a_depende"
            "ncy"
        ),
        (
            "tests/test_mutation38_kid_death_replan_contracts.py::test_ad"
            "ult_death_outside_the_birth_cohort_reports_no_dam_replan"
        ),
        (
            "tests/test_mutation38_kid_death_replan_contracts.py::test_de"
            "ad_dam_with_a_held_dependent_child_gets_no_recovery_plan"
        ),
        (
            "tests/test_mutation38_kid_death_replan_contracts.py::test_ki"
            "d_death_returns_a_retry_conflict_before_a_real_dam_retiremen"
            "t_commits"
        ),
        (
            "tests/test_mutation38_kid_death_replan_contracts.py::test_la"
            "test_litter_mortality_sets_recovery_and_reuses_a_retained_pe"
            "nding_plan"
        ),
        (
            "tests/test_mutation38_kid_death_replan_contracts.py::test_ne"
            "wer_litter_death_sweeps_its_duty_after_an_older_unrelated_du"
            "ty"
        ),
        (
            "tests/test_mutation38_kid_death_replan_contracts.py::test_pr"
            "elink_weaning_fallback_matches_the_litter_date_and_keeps_swe"
            "eping"
        ),
        (
            "tests/test_mutation38_kid_death_replan_contracts.py::test_tw"
            "o_other_dependent_kids_keep_the_dam_and_their_own_weaning_pl"
            "an"
        ),
        (
            "tests/test_mutation38_kid_death_replan_contracts.py::test_tw"
            "o_real_recovery_exits_preserve_an_adults_original_birth_outc"
            "ome"
        ),
        (
            "tests/test_mutation38_kid_death_replan_contracts.py::test_un"
            "related_weaning_history_does_not_hide_a_real_dependent_litte"
            "r"
        ),
        (
            "tests/test_mutation38_kid_death_retained_task_race.py::test_"
            "last_kid_death_preserves_a_concurrent_native_skip_of_a_retai"
            "ned_manual_duty"
        ),
    ),
    ("app/services/notifications/service.py", "_adopt_legacy_claim"): (
        (
            "tests/test_mutation38_notification_legacy_fact_contracts.py:"
            ":test_real_confirmed_fact_adopts_its_retained_receipt_before"
            "_cross_day_replay[SENT-0]"
        ),
    ),
    ("app/services/notifications/service.py", "_delivery_limiters"): (
        (
            "tests/test_mutation38_notification_delivery_contracts.py::te"
            "st_delivery_admission_conserves_separate_global_and_farm_all"
            "owances"
        ),
    ),
    ("app/services/notifications/service.py", "_digest_text_for_recipient"): (
        (
            "tests/test_mutation38_notification_deferred_replays.py::test"
            "_mixed_today_and_overdue_digest_counts_only_the_one_overdue_"
            "duty"
        ),
        (
            "tests/test_mutation38_notification_delivery_contracts.py::te"
            "st_digest_literal_body_preserves_five_titles_and_sixty_chara"
            "cter_bound[today]"
        ),
    ),
    ("app/services/notifications/service.py", "_send_notification_admitted"): (
        (
            "tests/test_mutation38_notification_claim_contracts.py::test_"
            "real_distinct_facts_do_not_alias_when_the_first_claim_is_rep"
            "layed[paid]"
        ),
        (
            "tests/test_mutation38_notification_claim_contracts.py::test_"
            "real_distinct_facts_do_not_alias_when_the_first_claim_is_rep"
            "layed[quiet-placeholder]"
        ),
        (
            "tests/test_mutation38_notification_deferred_replays.py::test"
            "_already_paid_fact_keeps_sent_receipt_when_replayed_during_q"
            "uiet_hours"
        ),
        (
            "tests/test_mutation38_notification_deferred_replays.py::test"
            "_first_daily_cap_refusal_is_fresh_and_its_same_fact_replay_i"
            "s_not"
        ),
        (
            "tests/test_mutation38_notification_delivery_contracts.py::te"
            "st_actual_delivered_claim_replays_without_repeating_the_prov"
            "ider"
        ),
        (
            "tests/test_mutation38_notification_farm_cap_race.py::test_tw"
            "o_real_recipient_claims_cannot_exceed_one_farm_paid_send"
        ),
        (
            "tests/test_mutation38_notification_legacy_fact_contracts.py:"
            ":test_real_confirmed_fact_adopts_its_retained_receipt_before"
            "_cross_day_replay[SENT-0]"
        ),
        (
            "tests/test_mutation38_notification_settlement_retention.py::"
            "test_paid_legacy_delivery_can_finish_while_retention_crosses"
            "_its_policy_cutoff"
        ),
    ),
    ("app/services/notifications/service.py", "_send_with_retry"): (
        (
            "tests/test_mutation38_notification_delivery_contracts.py::te"
            "st_actual_retry_pacing_increases_after_each_proven_pre_send_"
            "failure"
        ),
        (
            "tests/test_mutation38_notification_delivery_contracts.py::te"
            "st_one_attempt_budget_does_not_repeat_an_exhausted_safe_fail"
            "ure"
        ),
    ),
    ("app/services/notifications/service.py", "_wait_for_claim_to_settle"): (
        (
            "tests/test_mutation38_notification_claim_contracts.py::test_"
            "cached_loser_observes_the_actual_committed_provider_settleme"
            "nt"
        ),
        (
            "tests/test_mutation38_notification_claim_contracts.py::test_"
            "real_interrupted_claim_wait_stops_at_the_exact_stated_slack_"
            "boundary"
        ),
        (
            "tests/test_mutation38_notification_delivery_contracts.py::te"
            "st_actual_delivered_claim_replays_without_repeating_the_prov"
            "ider"
        ),
    ),
    ("app/services/notifications/service.py", "farms_ready_for_digest"): (
        (
            "tests/test_mutation38_notification_native_cycle_contracts.py"
            "::test_digest_readiness_respects_public_account_tombstone_be"
            "fore_membership_cleanup"
        ),
    ),
    ("app/services/notifications/service.py", "feed_reorder_daily"): (
        (
            "tests/test_mutation38_notification_native_cycle_contracts.py"
            "::test_empty_native_alert_scan_reports_zero_actual_deliverie"
            "s"
        ),
        (
            "tests/test_mutation38_notification_reorder_boundary_contract"
            ".py::test_supported_purchase_to_exact_reorder_level_has_no_b"
            "elow_level_alert"
        ),
    ),
    ("app/services/notifications/service.py", "kidding_watch_daily"): (
        (
            "tests/test_mutation38_notification_native_cycle_contracts.py"
            "::test_empty_native_alert_scan_reports_zero_actual_deliverie"
            "s"
        ),
    ),
    ("app/services/notifications/service.py", "notify_alert_class"): (
        (
            "tests/test_mutation38_notification_native_fanout_contracts.p"
            "y::test_native_vendor_body_failure_waits_for_successful_reci"
            "pient_settlement"
        ),
        (
            "tests/test_mutation38_notification_native_fanout_contracts.p"
            "y::test_valid_native_fanout_returns_one_fresh_delivery"
        ),
    ),
    ("app/services/notifications/service.py", "overdue_critical_sweep"): (
        (
            "tests/test_mutation38_notification_native_cycle_contracts.py"
            "::test_empty_native_alert_scan_reports_zero_actual_deliverie"
            "s"
        ),
        (
            "tests/test_mutation38_notification_native_fanout_contracts.p"
            "y::test_native_critical_sweep_counts_only_this_farms_pending"
            "_old_duties"
        ),
    ),
    ("app/services/notifications/service.py", "run_digest_for_farm"): (
        (
            "tests/test_mutation38_notification_native_cycle_contracts.py"
            "::test_production_digest_caller_uses_default_farm_clock_and_"
            "settles_one_delivery"
        ),
        (
            "tests/test_mutation38_notification_native_fanout_contracts.p"
            "y::test_native_vendor_body_failure_waits_for_successful_reci"
            "pient_settlement"
        ),
        (
            "tests/test_mutation38_notification_native_fanout_contracts.p"
            "y::test_valid_native_fanout_returns_one_fresh_delivery"
        ),
    ),
    ("app/services/notifications/service.py", "send_one"): (
        (
            "tests/test_mutation38_notification_deleted_queued_recipient_"
            "v1.py::test_native_fanout_skips_a_recipient_deleted_while_wa"
            "iting_for_a_delivery_slot"
        ),
    ),
    ("app/services/notifications/service.py", "settle"): (
        (
            "tests/test_mutation38_notification_claim_contracts.py::test_"
            "real_distinct_facts_do_not_alias_when_the_first_claim_is_rep"
            "layed[paid]"
        ),
        (
            "tests/test_mutation38_notification_delivery_contracts.py::te"
            "st_provider_failure_receipt_preserves_exact_redacted_error_b"
            "ound"
        ),
    ),
    ("app/services/purchases.py", "_purchase_batch_tag"): (
        (
            "tests/test_mutation38_livestock_native_purchase_tag.py::test"
            "_native_purchase_tag_accepts_fifty_characters_and_rejects_ab"
            "ove"
        ),
    ),
    ("app/services/purchases.py", "create_purchase_batch"): (
        (
            "tests/test_mutation38_livestock_native_purchase_bounds.py::t"
            "est_native_purchase_domain_bounds_and_rejection_leave_no_par"
            "tial_graph"
        ),
        (
            "tests/test_mutation38_livestock_native_purchase_weight_concu"
            "rrency.py::test_native_mutable_arrival_list_cannot_commit_a_"
            "shortened_weight_cohort"
        ),
        (
            "tests/test_mutation38_livestock_purchase_small_weights.py::t"
            "est_small_positive_purchase_weights_preserve_each_arrival_ba"
            "seline"
        ),
    ),
    ("app/services/screening/budget.py", "reserve_provider_attempt"): (
        (
            "tests/test_mutation38_screening_budget_native_contracts.py::"
            "test_native_attempt_replay_preserves_its_paid_scope_and_disa"
            "bled_cap"
        ),
    ),
    ("app/services/screening/detect.py", "clamped"): (
        (
            "tests/test_mutation38_detection_native_clamp.py::test_native"
            "_box_clamp_keeps_one_unit_and_repairs_empty_height[empty-coo"
            "rdinate-noise]"
        ),
        (
            "tests/test_mutation38_detection_native_clamp.py::test_native"
            "_box_clamp_keeps_one_unit_and_repairs_empty_height[small-pos"
            "itive-extent]"
        ),
        (
            "tests/test_mutation38_screening_detection_geometry.py::test_"
            "provider_coordinate_overflow_clamps_to_the_literal_1000_unit"
            "_frame"
        ),
    ),
    ("app/services/screening/detect.py", "parse_detection_response"): (
        (
            "tests/test_mutation38_detection_minimum_box_contracts.py::te"
            "st_literal_minimum_box_keeps_valid_evidence_and_drops_coordi"
            "nate_noise[width-below]"
        ),
        (
            "tests/test_mutation38_screening_detection_geometry.py::test_"
            "malformed_tuple_does_not_hide_the_following_real_goat[short]"
        ),
        (
            "tests/test_mutation38_screening_detection_geometry.py::test_"
            "supported_normalized_box_must_not_emit_a_sub_eight_pixel_cro"
            "p[narrow]"
        ),
        (
            "tests/test_mutation38_screening_detection_geometry.py::test_"
            "supported_normalized_box_must_not_emit_a_sub_eight_pixel_cro"
            "p[short]"
        ),
    ),
    ("app/services/screening/gate.py", "_extract_json_object"): (
        (
            "tests/test_mutation38_screening_gate_contracts.py::test_bala"
            "nced_json_extraction_preserves_plain_json_strings_before_adj"
            "acent_suffix[empty-json-member]"
        ),
        (
            "tests/test_mutation38_screening_gate_contracts.py::test_bala"
            "nced_json_extraction_preserves_plain_json_strings_before_adj"
            "acent_suffix[quoted-json-member]"
        ),
        (
            "tests/test_mutation38_screening_json_quoted_brace.py::test_c"
            "losing_brace_in_plain_clinical_note_does_not_end_the_provide"
            "r_object"
        ),
    ),
    ("app/services/screening/images.py", "crop_image"): (
        (
            "tests/test_mutation38_screening_detection_geometry.py::test_"
            "crop_coordinates_use_the_literal_1000_scale_and_five_percent"
            "_frame_margin"
        ),
        (
            "tests/test_mutation38_screening_detection_geometry.py::test_"
            "solid_crop_canonical_jpeg_has_a_small_content_optimized_huff"
            "man_alphabet"
        ),
        (
            "tests/test_mutation38_screening_detection_geometry.py::test_"
            "supported_normalized_box_must_not_emit_a_sub_eight_pixel_cro"
            "p[narrow]"
        ),
        (
            "tests/test_mutation38_screening_detection_geometry.py::test_"
            "supported_normalized_box_must_not_emit_a_sub_eight_pixel_cro"
            "p[short]"
        ),
    ),
    ("app/services/screening/images.py", "normalize_image"): (
        (
            "tests/test_mutation38_normalization_warning_race.py::test_co"
            "ncurrent_real_jpeg_normalizations_reject_over_budget_pixels"
        ),
    ),
    ("app/services/screening/pipeline.py", "_add_finding"): (
        (
            "tests/test_mutation38_screening_clinical_orchestration.py::t"
            "est_native_finding_builder_bounds_text_and_conserves_nullabl"
            "e_three_decimal_evidence[absent-bounded-native-label]"
        ),
        (
            "tests/test_mutation38_screening_clinical_orchestration.py::t"
            "est_native_finding_builder_bounds_text_and_conserves_nullabl"
            "e_three_decimal_evidence[absent-column-boundary]"
        ),
        (
            "tests/test_mutation38_screening_clinical_orchestration.py::t"
            "est_native_finding_builder_bounds_text_and_conserves_nullabl"
            "e_three_decimal_evidence[precise-column-boundary]"
        ),
    ),
    ("app/services/screening/pipeline.py", "_claim_retry_rows"): (
        (
            "tests/test_mutation38_screening_authoritative_claim_takeover"
            "_v2.py::test_failed_stalled_owner_takeover_retains_claim_on_"
            "due_changed_raw_retry"
        ),
        (
            "tests/test_mutation38_screening_queue_budget_cohorts.py::tes"
            "t_native_claim_skips_a_real_owned_row_and_claims_the_free_ph"
            "oto"
        ),
        (
            "tests/test_mutation38_screening_queue_budget_cohorts.py::tes"
            "t_native_claims_conserve_local_legacy_and_durable_admission"
        ),
        (
            "tests/test_mutation38_screening_queue_native_contracts.py::t"
            "est_native_claim_priority_serves_an_unvisited_farm_before_a_"
            "recently_served_farm"
        ),
        (
            "tests/test_mutation38_screening_queue_native_contracts.py::t"
            "est_native_queue_respects_upload_lease_and_crop_retry_bounda"
            "ries"
        ),
    ),
    ("app/services/screening/pipeline.py", "_enqueue_healthy_control"): (
        (
            "tests/test_mutation38_screening_scoreboard_export_contracts."
            "py::test_actual_hash_sample_preserves_confidence_and_one_nat"
            "ive_neutral_review"
        ),
    ),
    ("app/services/screening/pipeline.py", "_expire_abandoned_uploads"): (
        (
            "tests/test_mutation38_screening_queue_native_contracts.py::t"
            "est_maintenance_keeps_rows_exactly_at_their_deadline"
        ),
        (
            "tests/test_mutation38_screening_queue_native_contracts.py::t"
            "est_maintenance_skips_an_owned_row_lease_and_retires_the_fre"
            "e_peer"
        ),
    ),
    ("app/services/screening/pipeline.py", "_note_object_absent"): (
        (
            "tests/test_mutation38_screening_cycle_outcome_contracts.py::"
            "test_slow_live_upload_refunds_only_its_current_probe_attempt"
        ),
    ),
    ("app/services/screening/pipeline.py", "_prior_coverage_safety_status"): (
        (
            "tests/test_mutation38_screening_clinical_orchestration.py::t"
            "est_native_retained_multiple_safety_passes_reuse_the_latest_"
            "real_gate"
        ),
    ),
    ("app/services/screening/pipeline.py", "_process_image"): (
        (
            "tests/test_mutation38_screening_authoritative_claim_takeover"
            "_v2.py::test_failed_stalled_owner_takeover_retains_claim_on_"
            "due_changed_raw_retry"
        ),
        (
            "tests/test_mutation38_screening_authoritative_content_claim_"
            "contracts.py::test_durable_content_claim_keeps_its_identity_"
            "and_counts_one_rejection"
        ),
        (
            "tests/test_mutation38_screening_crop_control_completion.py::"
            "test_failed_first_crop_upload_does_not_leave_later_goat_pend"
            "ing"
        ),
        (
            "tests/test_mutation38_screening_crop_control_completion.py::"
            "test_neutral_control_waits_for_parent_and_uses_its_whole_fra"
            "me_evidence"
        ),
        (
            "tests/test_mutation38_screening_crop_control_completion.py::"
            "test_unusable_first_box_does_not_leave_later_goat_pending"
        ),
        (
            "tests/test_mutation38_screening_crop_control_completion.py::"
            "test_whole_frame_gate_outage_counts_one_parent_error"
        ),
        (
            "tests/test_mutation38_screening_cycle_outcome_contracts.py::"
            "test_single_real_photo_has_exact_clinical_and_cycle_totals[E"
            "RROR-detector-empty]"
        ),
        (
            "tests/test_mutation38_screening_cycle_outcome_contracts.py::"
            "test_single_real_photo_has_exact_clinical_and_cycle_totals[H"
            "EALTHY-detector-empty]"
        ),
        (
            "tests/test_mutation38_screening_cycle_outcome_contracts.py::"
            "test_single_real_photo_has_exact_clinical_and_cycle_totals[H"
            "EALTHY-whole-frame]"
        ),
        (
            "tests/test_mutation38_screening_cycle_outcome_contracts.py::"
            "test_single_real_photo_has_exact_clinical_and_cycle_totals[U"
            "NASSESSABLE-detector-empty]"
        ),
        (
            "tests/test_mutation38_screening_cycle_outcome_contracts.py::"
            "test_storage_frontier_contributes_one_truthful_failure_and_n"
            "o_clinical_calls[changed-derivative-ERROR-normalized screeni"
            "ng derivative failed its integrity check]"
        ),
        (
            "tests/test_mutation38_screening_cycle_outcome_contracts.py::"
            "test_storage_frontier_contributes_one_truthful_failure_and_n"
            "o_clinical_calls[metadata-ERROR-DOWNLOAD_FAILED]"
        ),
        (
            "tests/test_mutation38_screening_cycle_outcome_contracts.py::"
            "test_storage_frontier_contributes_one_truthful_failure_and_n"
            "o_clinical_calls[stream-oversize-SKIPPED-OBJECT_TOO_LARGE]"
        ),
        (
            "tests/test_mutation38_screening_cycle_outcome_contracts.py::"
            "test_storage_frontier_contributes_one_truthful_failure_and_n"
            "o_clinical_calls[upload-ERROR-DOWNLOAD_FAILED]"
        ),
        (
            "tests/test_mutation38_screening_cycle_outcome_contracts.py::"
            "test_storage_frontier_contributes_one_truthful_failure_and_n"
            "o_clinical_calls[wrong-type-SKIPPED-object content type does"
            " not match its pre-registration]"
        ),
        (
            "tests/test_mutation38_screening_native_crop_persistence_cont"
            "ract.py::test_native_detected_goats_commit_crop_identities_a"
            "nd_their_own_evidence"
        ),
        (
            "tests/test_mutation38_screening_retained_coverage_control_v2"
            ".py::test_retained_native_coverage_history_keeps_latest_neut"
            "ral_review_provenance"
        ),
        (
            "tests/test_mutation38_screening_retained_raw_and_duplicate_c"
            "ontracts.py::test_constraint_valid_legacy_terminal_results_k"
            "eep_tenant_deduplication"
        ),
        (
            "tests/test_mutation38_screening_retained_raw_and_duplicate_c"
            "ontracts.py::test_exact_upload_byte_allowance_is_screened_no"
            "rmally"
        ),
        (
            "tests/test_mutation38_screening_retained_raw_and_duplicate_c"
            "ontracts.py::test_retained_raw_content_identity_rejects_a_ch"
            "anged_upload"
        ),
    ),
    ("app/services/screening/pipeline.py", "_record_failed_gate_attempt"): (
        (
            "tests/test_mutation38_screening_clinical_orchestration.py::t"
            "est_failed_rotation_journals_each_actual_provider_model_and_"
            "paid_attempt"
        ),
    ),
    ("app/services/screening/pipeline.py", "_record_run"): (
        (
            "tests/test_mutation38_screening_confidence_double_rounding_c"
            "ontracts.py::test_native_run_rounds_confidence_once_before_n"
            "umeric_storage"
        ),
        (
            "tests/test_mutation38_screening_run_native_contracts.py::tes"
            "t_native_run_bounds_a_real_optional_error_diagnostic_to_two_"
            "thousand_characters"
        ),
        (
            "tests/test_mutation38_screening_run_native_contracts.py::tes"
            "t_native_run_rounds_confidence_once_before_numeric_storage"
        ),
    ),
    ("app/services/screening/pipeline.py", "_reserve_normalized_content"): (
        (
            "tests/test_mutation38_screening_content_claim_contracts.py::"
            "test_duplicate_candidate_does_not_gain_an_ownership_marker_o"
            "r_commit_pending_work"
        ),
        (
            "tests/test_mutation38_screening_content_claim_contracts.py::"
            "test_fresh_tombstone_reservation_ignores_an_unrelated_same_f"
            "arm_claim"
        ),
        (
            "tests/test_mutation38_screening_content_claim_contracts.py::"
            "test_one_image_never_rebinds_its_existing_claim_to_changed_n"
            "ormalized_bytes"
        ),
        (
            "tests/test_mutation38_screening_content_claim_contracts.py::"
            "test_reobserved_canonical_digest_repairs_an_initially_missin"
            "g_image_marker"
        ),
        (
            "tests/test_mutation38_screening_peer_claim_transfer_guard_v2"
            ".py::test_stale_reservation_cannot_steal_a_peer_committed_li"
            "ve_claim"
        ),
    ),
    ("app/services/screening/pipeline.py", "_resolve_content_claim_conflict"): (
        (
            "tests/test_mutation38_screening_content_claim_contracts.py::"
            "test_cascade_deleted_owner_is_replaced_by_a_new_durable_clai"
            "m_after_actual_retention"
        ),
        (
            "tests/test_mutation38_screening_content_claim_contracts.py::"
            "test_stale_owner_argument_reloads_the_actual_retention_takeo"
            "ver_winner"
        ),
        (
            "tests/test_mutation38_screening_content_claim_contracts.py::"
            "test_tombstone_conflict_transfers_without_rewriting_the_fenc"
            "ed_prior_result"
        ),
    ),
    ("app/services/screening/pipeline.py", "_run_cascade"): (
        (
            "tests/test_mutation38_screening_clinical_orchestration.py::t"
            "est_failed_rotation_journals_each_actual_provider_model_and_"
            "paid_attempt"
        ),
        (
            "tests/test_mutation38_screening_clinical_orchestration.py::t"
            "est_native_provider_answer_alias_preserves_every_unresolved_"
            "observation"
        ),
        (
            "tests/test_mutation38_screening_clinical_orchestration.py::t"
            "est_one_specialist_outage_preserves_its_region_and_continues"
            "_the_next_kind"
        ),
        (
            "tests/test_mutation38_screening_clinical_orchestration.py::t"
            "est_real_paid_budget_defers_once_and_preserves_gate_provenan"
            "ce_and_regions[cross-check-denied]"
        ),
        (
            "tests/test_mutation38_screening_clinical_orchestration.py::t"
            "est_real_paid_budget_defers_once_and_preserves_gate_provenan"
            "ce_and_regions[refinement-denied]"
        ),
        (
            "tests/test_mutation38_screening_clinical_orchestration.py::t"
            "est_unassessable_real_cross_check_does_not_claim_clinical_ag"
            "reement"
        ),
    ),
    ("app/services/screening/pipeline.py", "_terminate_budget_exhausted_processing"): (
        (
            "tests/test_mutation38_screening_queue_native_contracts.py::t"
            "est_maintenance_keeps_rows_exactly_at_their_deadline"
        ),
        (
            "tests/test_mutation38_screening_queue_native_contracts.py::t"
            "est_maintenance_skips_an_owned_row_lease_and_retires_the_fre"
            "e_peer"
        ),
    ),
    ("app/services/screening/pipeline.py", "parse_raw_key"): (
        (
            "tests/test_mutation38_screening_queue_native_contracts.py::t"
            "est_retained_raw_key_diagnostic_preserves_its_real_capture_d"
            "ate"
        ),
    ),
    ("app/services/screening/pipeline.py", "run_screening_cycle"): (
        (
            "tests/test_mutation38_screening_cycle_failure_contracts.py::"
            "test_real_transport_denial_refunds_only_the_claim_and_keeps_"
            "it_retryable"
        ),
        (
            "tests/test_mutation38_screening_cycle_failure_contracts.py::"
            "test_terminal_provider_error_keeps_its_real_safe_failure_rea"
            "son"
        ),
        (
            "tests/test_mutation38_screening_rollback_history_availabilit"
            "y.py::test_prior_rollback_does_not_lease_a_later_photos_hist"
            "ory_during_remote_head"
        ),
    ),
    ("app/services/screening/providers.py", "__init__"): (
        (
            "tests/test_mutation38_screening_adapter_wire_contracts.py::t"
            "est_native_adapter_preserves_caller_transport_answer_and_ela"
            "psed_milliseconds[anthropic]"
        ),
        (
            "tests/test_mutation38_screening_adapter_wire_contracts.py::t"
            "est_native_adapter_preserves_caller_transport_answer_and_ela"
            "psed_milliseconds[openai]"
        ),
        (
            "tests/test_mutation38_screening_adapter_wire_contracts.py::t"
            "est_native_rotation_entry_honors_its_endpoint_or_the_configu"
            "red_fallback[False-anthropic]"
        ),
        (
            "tests/test_mutation38_screening_adapter_wire_contracts.py::t"
            "est_native_rotation_entry_honors_its_endpoint_or_the_configu"
            "red_fallback[False-openai]"
        ),
    ),
    ("app/services/screening/providers.py", "_admit_retry"): (
        (
            "tests/test_mutation38_screening_retry_http_boundary.py::test"
            "_transient_http_boundary_preserves_exact_paid_retry_admissio"
            "n[first-server-error-anthropic]"
        ),
    ),
    ("app/services/screening/providers.py", "_post_with_one_retry"): (
        (
            "tests/test_mutation38_screening_retry_http_boundary.py::test"
            "_transient_http_boundary_preserves_exact_paid_retry_admissio"
            "n[first-server-error-anthropic]"
        ),
        (
            "tests/test_mutation38_screening_retry_http_boundary.py::test"
            "_transient_http_boundary_preserves_exact_paid_retry_admissio"
            "n[last-client-error-anthropic]"
        ),
    ),
    ("app/services/screening/providers.py", "build_provider_rotation"): (
        (
            "tests/test_mutation38_screening_adapter_wire_contracts.py::t"
            "est_configured_native_provider_builder_preserves_vendor_wire"
            "_and_rotation_order[rotation]"
        ),
        (
            "tests/test_mutation38_screening_adapter_wire_contracts.py::t"
            "est_configured_native_provider_builder_preserves_vendor_wire"
            "_and_rotation_order[single-anthropic]"
        ),
    ),
    ("app/services/screening/providers.py", "complete"): (
        (
            "tests/test_mutation38_screening_adapter_wire_contracts.py::t"
            "est_native_adapter_preserves_caller_transport_answer_and_ela"
            "psed_milliseconds[anthropic]"
        ),
        (
            "tests/test_mutation38_screening_adapter_wire_contracts.py::t"
            "est_native_adapter_preserves_caller_transport_answer_and_ela"
            "psed_milliseconds[openai]"
        ),
    ),
    ("app/services/screening/rotation.py", "__init__"): (
        (
            "tests/test_mutation38_screening_rotation_native_contracts.py"
            "::test_native_served_gate_chooses_the_next_usable_configured"
            "_second_opinion[day-primary]"
        ),
    ),
    ("app/services/screening/rotation.py", "cross_checker_for"): (
        (
            "tests/test_mutation38_screening_rotation_native_contracts.py"
            "::test_native_served_gate_chooses_the_next_usable_configured"
            "_second_opinion[day-primary]"
        ),
        (
            "tests/test_mutation38_screening_rotation_native_contracts.py"
            "::test_native_served_gate_chooses_the_next_usable_configured"
            "_second_opinion[native-backup]"
        ),
    ),
    ("app/services/screening/s3.py", "_build_client"): (
        (
            "tests/test_mutation38_screening_storage_native_contracts.py:"
            ":test_disabled_runtime_configuration_still_fails_closed_at_n"
            "ative_storage[missing]"
        ),
        (
            "tests/test_mutation38_screening_storage_native_contracts.py:"
            ":test_real_clients_apply_the_finite_worker_socket_and_retry_"
            "policy"
        ),
    ),
    ("app/services/screening/s3.py", "bucket"): (
        (
            "tests/test_mutation38_screening_storage_native_contracts.py:"
            ":test_disabled_runtime_configuration_still_fails_closed_at_n"
            "ative_storage[missing]"
        ),
    ),
    ("app/services/screening/s3.py", "delete_permanently"): (
        (
            "tests/test_mutation38_screening_storage_native_contracts.py:"
            ":test_versioned_delete_does_not_finalize_an_acknowledged_par"
            "tial_failure"
        ),
    ),
    ("app/services/screening/s3.py", "download"): (
        (
            "tests/test_mutation38_screening_storage_native_contracts.py:"
            ":test_declared_download_retries_distinguish_missing_and_chan"
            "ged_snapshots[snapshot-replaced]"
        ),
        (
            "tests/test_mutation38_screening_storage_native_contracts.py:"
            ":test_declared_download_retries_distinguish_missing_and_chan"
            "ged_snapshots[upload-not-yet-present]"
        ),
        (
            "tests/test_mutation38_screening_storage_native_contracts.py:"
            ":test_download_deadline_is_closed_at_twenty_seconds[at-deadl"
            "ine]"
        ),
        (
            "tests/test_mutation38_screening_storage_native_contracts.py:"
            ":test_incompatible_get_header_cannot_bypass_the_stream_memor"
            "y_ceiling"
        ),
        (
            "tests/test_mutation38_screening_storage_native_contracts.py:"
            ":test_one_byte_download_keeps_the_inspected_immutable_snapsh"
            "ot[etag]"
        ),
        (
            "tests/test_mutation38_screening_storage_native_contracts.py:"
            ":test_one_byte_download_keeps_the_inspected_immutable_snapsh"
            "ot[version]"
        ),
        (
            "tests/test_mutation38_screening_storage_native_contracts.py:"
            ":test_zero_download_allowance_rejects_before_any_transport_r"
            "equest"
        ),
    ),
    ("app/services/screening/s3.py", "object_info"): (
        (
            "tests/test_mutation38_screening_storage_native_contracts.py:"
            ":test_head_does_not_misclassify_provider_denial_as_a_missing"
            "_upload"
        ),
        (
            "tests/test_mutation38_screening_storage_native_contracts.py:"
            ":test_head_preserves_real_response_snapshot_metadata"
        ),
    ),
    ("app/services/screening/s3.py", "presign_post"): (
        (
            "tests/test_mutation38_screening_presign_admission.py::test_o"
            "ne_byte_object_allowance_produces_a_usable_signed_browser_po"
            "licy"
        ),
        (
            "tests/test_mutation38_screening_presign_admission.py::test_z"
            "ero_byte_object_allowance_cannot_mint_a_browser_upload_form"
        ),
    ),
    ("app/services/screening/s3.py", "storage_for_settings"): (
        (
            "tests/test_mutation38_screening_storage_native_contracts.py:"
            ":test_real_settings_cache_preserves_its_thirty_two_instance_"
            "fifo_window"
        ),
    ),
    ("app/services/screening/specialists.py", "parse_specialist_response"): (
        (
            "tests/test_mutation38_screening_specialist_parser_contracts."
            "py::test_specialist_disease_guess_boundaries_preserve_valid_"
            "original_codes"
        ),
        (
            "tests/test_mutation38_screening_specialist_parser_contracts."
            "py::test_unknown_specialist_disease_keeps_the_original_guess"
            "_and_bounded_note"
        ),
    ),
    ("app/services/simulation_calibration.py", "_curve_from_observations"): (
        (
            "tests/test_mutation38_simulation_calibration_native_evidence"
            "_v3.py::test_legal_low_weight_measurements_produce_a_nondecr"
            "easing_native_curve"
        ),
    ),
    ("app/services/simulation_calibration.py", "_deflated"): (
        (
            "tests/test_mutation38_simulation_calibration_growth_observat"
            "ions_v1.py::test_actual_festival_sales_remove_the_premium_be"
            "fore_calibrating_the_plain_base_price"
        ),
    ),
    ("app/services/simulation_calibration.py", "_isotonic_fit"): (
        (
            "tests/test_mutation38_simulation_calibration_native_evidence"
            "_v3.py::test_actual_isotonic_growth_fit_conserves_equal_obse"
            "rvation_weight"
        ),
    ),
    ("app/services/simulation_calibration.py", "_months_between"): (
        (
            "tests/test_mutation38_simulation_calibration_native_evidence"
            "_v3.py::test_native_ledger_month_fraction_matches_actual_ela"
            "psed_engine_days"
        ),
    ),
    ("app/services/simulation_calibration.py", "calibrate_farm_assumptions"): (
        (
            "tests/test_mutation38_simulation_calibration_exit_weight_win"
            "dow_v4.py::test_actual_sales_accept_thirty_day_weight_and_ex"
            "clude_older_and_post_exit_weights"
        ),
        (
            "tests/test_mutation38_simulation_calibration_growth_observat"
            "ions_v1.py::test_actual_credible_large_live_birth_weights_ra"
            "ise_only_decreasing_early_growth_ages"
        ),
        (
            "tests/test_mutation38_simulation_calibration_growth_observat"
            "ions_v1.py::test_actual_five_weights_at_three_ages_include_t"
            "he_lookback_boundary_and_known_births"
        ),
        (
            "tests/test_mutation38_simulation_calibration_growth_observat"
            "ions_v1.py::test_actual_one_animal_snapshot_is_complete_and_"
            "reports_high_cohort_confidence"
        ),
        (
            "tests/test_mutation38_simulation_calibration_mature_births_v"
            "2.py::test_actual_births_at_completed_weaning_and_stillbirth"
            "_ceiling_are_reported_truthfully"
        ),
        (
            "tests/test_mutation38_simulation_calibration_native_evidence"
            "_v3.py::test_empty_native_farm_calibration_returns_zero_open"
            "ing_cohorts_with_auditable_evidence"
        ),
        (
            "tests/test_mutation38_simulation_calibration_native_evidence"
            "_v3.py::test_native_cohort_and_feed_cost_evidence_matches_co"
            "mplete_actual_current_ledger"
        ),
        (
            "tests/test_mutation38_simulation_calibration_qualified_obser"
            "vations_v4.py::test_actual_dated_deaths_use_complete_owned_e"
            "xposure_in_each_native_age_class"
        ),
        (
            "tests/test_mutation38_simulation_calibration_qualified_obser"
            "vations_v4.py::test_current_native_herd_cohorts_use_calendar"
            "_birthdays_and_estimated_dob"
        ),
        (
            "tests/test_mutation38_simulation_calibration_qualified_obser"
            "vations_v4.py::test_five_actual_kiddings_calibrate_ten_kids_"
            "with_live_birth_and_phase_evidence"
        ),
    ),
    ("app/services/simulation_calibration.py", "record"): (
        (
            "tests/test_mutation38_simulation_calibration_qualified_obser"
            "vations_v4.py::test_native_structured_feed_evidence_confiden"
            "ce_follows_supported_observation_bands"
        ),
    ),
    ("app/services/tasks.py", "_guard_generated_movement_task"): (
        (
            "tests/test_mutation38_livestock_pregnancy_movement_guards.py"
            "::test_restored_delivery_duty_cannot_move_without_its_author"
            "ity_and_calendar"
        ),
    ),
    ("app/services/tasks.py", "_guard_generated_weaning_task"): (
        (
            "tests/test_mutation38_weaning_retained_native_contracts.py::"
            "test_legacy_weaning_lookup_retains_the_oldest_actual_litter_"
            "when_provenance_is_duplicated"
        ),
    ),
    ("app/services/tasks.py", "_guard_quarantine_release"): (
        (
            "tests/test_mutation38_livestock_quarantine_release_guards.py"
            "::test_release_rejects_each_legacy_provenance_fault_or_singl"
            "e_movement_hold"
        ),
    ),
    ("app/services/tasks.py", "_linked_breeding_for_movement_task"): (
        (
            "tests/test_mutation38_livestock_pregnancy_movement_guards.py"
            "::test_restored_delivery_duty_cannot_move_without_its_author"
            "ity_and_calendar"
        ),
    ),
    ("app/services/tasks.py", "_litter_has_surviving_kid"): (
        (
            "tests/test_mutation38_livestock_task_survival_contracts.py::"
            "test_real_two_kid_litter_is_surviving_in_its_actual_farm"
        ),
        (
            "tests/test_mutation38_livestock_weaning_and_survival_facts.p"
            "y::test_survival_assessment_keeps_unknown_legacy_live_births"
            "_fail_closed"
        ),
    ),
    ("app/services/tasks.py", "complete_task"): (
        (
            "tests/test_mutation38_delivery_projected_native_contract.py:"
            ":test_native_delivery_completion_accepts_a_genuine_projected"
            "_locked_animal"
        ),
        (
            "tests/test_mutation38_livestock_task_survival_contracts.py::"
            "test_actual_no_survivor_postpartum_move_retains_the_completi"
            "ng_owner"
        ),
        (
            "tests/test_mutation38_livestock_weaning_and_survival_facts.p"
            "y::test_public_weaning_attributes_each_real_movement_to_the_"
            "completing_owner"
        ),
        (
            "tests/test_mutation38_livestock_weaning_dependency_contracts"
            ".py::test_last_litters_weaning_cannot_complete_while_its_mov"
            "ing_dam_is_held"
        ),
        (
            "tests/test_mutation38_livestock_weaning_dependency_contracts"
            ".py::test_older_litter_can_wean_while_two_newer_dependents_k"
            "eep_the_held_dam_in_recovery"
        ),
        (
            "tests/test_mutation38_livestock_weaning_dependency_contracts"
            ".py::test_retained_adult_daughters_actual_weaning_exit_prove"
            "s_she_is_no_longer_dependent"
        ),
        (
            "tests/test_mutation38_task_movement_native_guards.py::test_n"
            "ative_day_hundred_completion_without_explicit_date_uses_its_"
            "actual_farm"
        ),
        (
            "tests/test_mutation38_task_movement_native_guards.py::test_r"
            "etained_generated_movement_without_an_animal_link_is_a_confl"
            "ict"
        ),
        (
            "tests/test_mutation38_weaning_projection_guard.py::test_nati"
            "ve_weaning_reports_the_held_dam_with_a_real_projected_locked"
            "_kid"
        ),
        (
            "tests/test_mutation38_weaning_retained_native_contracts.py::"
            "test_native_weaning_without_its_prelocked_doe_is_a_guarded_c"
            "onflict"
        ),
    ),
    ("app/services/tasks.py", "find_live_recurring_successor"): (
        (
            "tests/test_mutation38_native_recurrence_boundaries.py::test_"
            "retained_multiple_pending_occurrences_reuse_the_earliest_rea"
            "l_successor"
        ),
    ),
    ("app/services/tasks.py", "resolve_personal_task_role_fallback"): (
        (
            "tests/test_mutation38_retained_personal_role.py::test_public"
            "_rejection_repairs_the_real_retained_personal_role"
        ),
        (
            "tests/test_mutation38_retained_personal_role.py::test_retain"
            "ed_role_repair_remains_available_under_a_shared_membership_l"
            "ease"
        ),
    ),
    ("app/services/tasks.py", "spawn_next_occurrence"): (
        (
            "tests/test_mutation38_native_recurrence_boundaries.py::test_"
            "native_retained_date_boundary_has_a_representable_successor_"
            "or_domain_error"
        ),
        (
            "tests/test_mutation38_native_recurrence_boundaries.py::test_"
            "native_review_repairs_retained_personal_role_before_pending_"
            "successor_insert"
        ),
        (
            "tests/test_mutation38_native_recurrence_boundaries.py::test_"
            "native_successor_uses_the_actual_farm_midnight_and_maximum_i"
            "nterval"
        ),
        (
            "tests/test_mutation38_native_recurrence_null_identity_warnin"
            "g.py::test_valid_native_conflict_completion_emits_no_null_ta"
            "sk_identity_misuse_warning"
        ),
        (
            "tests/test_mutation38_recurring_conflict_winner_contract.py:"
            ":test_valid_retained_recurring_conflict_resolves_exact_exist"
            "ing_winner"
        ),
        (
            "tests/test_mutation38_retained_personal_recurrence_role_cont"
            "ract.py::test_retained_terminal_personal_recurrence_repairs_"
            "role_and_spawns_one_successor"
        ),
    ),
    ("app/services/tasks.py", "task_scope"): (
        (
            "tests/test_mutation38_task_native_membership_scope_contracts"
            ".py::test_native_active_worker_sees_own_role_and_personal_du"
            "ties_only"
        ),
        (
            "tests/test_mutation38_task_native_membership_scope_contracts"
            ".py::test_public_deactivation_leaves_supported_helper_with_p"
            "ersonal_scope_only"
        ),
    ),
    ("app/services/tasks.py", "verify_task"): (
        (
            "tests/test_mutation38_task_native_default_review.py::test_na"
            "tive_verification_with_omitted_spawn_flag_continues_the_actu"
            "al_series"
        ),
        (
            "tests/test_mutation38_task_review_contracts.py::test_reviewe"
            "d_linked_recurrence_continues_only_while_its_animal_is_activ"
            "e"
        ),
    ),
    ("app/simulation/assumptions.py", "_adult_weight_above_yearling"): (
        (
            "tests/test_mutation38_simulation_complete_growth_admission.p"
            "y::test_whole_json_admits_an_adult_equal_to_yearling_and_rej"
            "ects_a_shrinking_anchor"
        ),
    ),
    ("app/simulation/assumptions.py", "_approved_nlm_unit_is_supported"): (
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_explicit_zero_award_books_no_funding_for_a_nonqualifying_"
            "unit"
        ),
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_nonqualifying_unit_cannot_receive_a_positive_approval"
        ),
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_qualifying_unit_admits_its_published_cap_without_inventin"
            "g_a_larger_unit"
        ),
    ),
    ("app/simulation/assumptions.py", "_bounded_meat_multipliers"): (
        (
            "tests/test_mutation38_simulation_market_calendar_admission.p"
            "y::test_authored_price_curve_rejects_nonpositive_and_excessi"
            "ve_months"
        ),
        (
            "tests/test_mutation38_simulation_market_calendar_admission.p"
            "y::test_maximum_monthly_stress_curve_is_admitted_and_normali"
            "zes_annual_mean"
        ),
    ),
    ("app/simulation/assumptions.py", "_bounded_monthly_multipliers"): (
        (
            "tests/test_mutation38_simulation_seasonal_feed_admission_v2."
            "py::test_json_seasonal_feed_values_admit_the_cap_and_reject_"
            "zero_negative_or_excess"
        ),
    ),
    ("app/simulation/assumptions.py", "_event_age_within_class_chain"): (
        (
            "tests/test_mutation38_simulation_complete_growth_admission.p"
            "y::test_default_age_of_an_earlier_event_does_not_hide_a_late"
            "r_invalid_arrival"
        ),
        (
            "tests/test_mutation38_simulation_empty_grower_arrivals.py::t"
            "est_empty_grower_arrival_is_exactly_the_six_month_graduation"
            "_boundary"
        ),
        (
            "tests/test_mutation38_simulation_growth_events.py::test_json"
            "_empty_grower_chain_retains_only_the_graduation_boundary"
        ),
        (
            "tests/test_mutation38_simulation_growth_events.py::test_json"
            "_event_at_the_final_projection_month_is_retained"
        ),
        (
            "tests/test_mutation38_simulation_growth_events.py::test_json"
            "_purchased_young_stock_stays_in_its_inclusive_biological_age"
            "_chain"
        ),
    ),
    ("app/simulation/assumptions.py", "_events_within_horizon"): (
        (
            "tests/test_mutation38_simulation_growth_events.py::test_json"
            "_event_at_the_final_projection_month_is_retained"
        ),
    ),
    ("app/simulation/assumptions.py", "_festival_months_within_horizon"): (
        (
            "tests/test_mutation38_simulation_forecast_calendar_native_v2"
            ".py::test_native_user_override_before_the_window_is_ignored_"
            "without_rejecting_the_valid_plan"
        ),
        (
            "tests/test_mutation38_simulation_market_calendar_admission.p"
            "y::test_authored_local_festival_dates_preserve_the_document_"
            "budget"
        ),
        (
            "tests/test_mutation38_simulation_market_calendar_admission.p"
            "y::test_explicit_festival_months_include_first_and_last_run_"
            "month_and_prune_later"
        ),
        (
            "tests/test_mutation38_simulation_market_calendar_admission.p"
            "y::test_explicit_festival_months_reject_invalid_run_position"
            "s"
        ),
        (
            "tests/test_mutation38_simulation_market_calendar_admission.p"
            "y::test_forty_authored_festival_months_survive_without_calen"
            "dar_replacement"
        ),
        (
            "tests/test_mutation38_simulation_market_calendar_admission.p"
            "y::test_local_date_override_translates_year_boundaries_and_p"
            "rices_only_covered_months"
        ),
    ),
    ("app/simulation/assumptions.py", "_financing_is_coherent"): (
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_approved_award_can_equal_half_the_eligible_budget_and_pre"
            "serves_installments"
        ),
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_declared_approval_needs_scheme_and_known_eligible_budget"
        ),
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_full_project_funding_is_admitted_without_negative_promote"
            "r_equity"
        ),
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_half_cent_receipt_tolerance_admits_its_exact_boundary"
        ),
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_receipts_require_a_real_positive_award"
        ),
    ),
    ("app/simulation/assumptions.py", "_foundation_age_window_is_valid"): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_json_integer_policy_retains_both_endpoints_and_rejects_o"
            "utside"
        ),
    ),
    ("app/simulation/assumptions.py", "_multipliers_are_positive"): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_json_parity_tables_support_one_through_twelve_and_the_fu"
            "ll_multiplier_range"
        ),
    ),
    ("app/simulation/assumptions.py", "_normalized_seasonality"): (
        (
            "tests/test_mutation38_simulation_market_calendar_admission.p"
            "y::test_native_neutral_curve_prices_all_twelve_calendar_mont"
            "hs"
        ),
    ),
    ("app/simulation/assumptions.py", "_planned_capacity_is_present"): (
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_user_approved_capacity_requires_an_actual_positive_suppli"
            "ed_plan"
        ),
    ),
    ("app/simulation/assumptions.py", "_range_order"): (
        (
            "tests/test_mutation38_simulation_optimizer_admission_v2.py::"
            "test_equal_herd_scale_bounds_pin_the_existing_herd_size"
        ),
    ),
    ("app/simulation/assumptions.py", "_valid_festival_dates"): (
        (
            "tests/test_mutation38_simulation_market_calendar_admission.p"
            "y::test_authored_local_festival_dates_preserve_the_document_"
            "budget"
        ),
    ),
    ("app/simulation/assumptions.py", "_valid_year_month"): (
        (
            "tests/test_mutation38_saved_decision_schema_boundaries_v1.py"
            "::test_meta_horizon_json_admission"
        ),
        (
            "tests/test_mutation38_saved_decision_schema_boundaries_v1.py"
            "::test_meta_real_calendar_json_admission"
        ),
        (
            "tests/test_mutation38_saved_decision_schema_boundaries_v1.py"
            "::test_saved_document_name_json_admission"
        ),
    ),
    ("app/simulation/assumptions.py", "_weight_curve_is_nondecreasing"): (
        (
            "tests/test_mutation38_simulation_growth_events.py::test_json"
            "_growth_curve_has_exactly_birth_through_month_twelve_and_per"
            "mits_plateaus"
        ),
    ),
    ("app/simulation/assumptions.py", "min_feasible_sale_age"): (
        (
            "tests/test_mutation38_minimum_feasible_sale_age_native_v2.py"
            "::test_native_sale_age_floor_preserves_whole_valid_purchase_"
            "document"
        ),
    ),
    ("app/simulation/backward_planner.py", "__init__"): (
        (
            "tests/test_mutation38_simulation_backward_biology_instructio"
            "ns_v3.py::test_real_class_window_dates_the_birth_that_reache"
            "s_mid_class_at_sale"
        ),
    ),
    ("app/simulation/backward_planner.py", "_effective_conception"): (
        (
            "tests/test_mutation38_simulation_backward_biology_instructio"
            "ns_v3.py::test_real_requirement_chain_accounts_for_survival_"
            "litter_and_single_service"
        ),
        (
            "tests/test_mutation38_simulation_backward_biology_instructio"
            "ns_v3.py::test_zero_conception_native_disposition_does_not_p"
            "romise_successful_births"
        ),
    ),
    ("app/simulation/backward_planner.py", "_purchase_actions"): (
        (
            "tests/test_mutation38_simulation_backward_biology_instructio"
            "ns_v3.py::test_real_purchase_actions_group_actual_head_and_d"
            "isclose_the_tightest_lead"
        ),
    ),
    ("app/simulation/backward_planner.py", "_requirement_chain"): (
        (
            "tests/test_mutation38_simulation_backward_biology_instructio"
            "ns_v3.py::test_impossible_requirement_chain_is_a_controlled_"
            "value_error"
        ),
        (
            "tests/test_mutation38_simulation_backward_biology_instructio"
            "ns_v3.py::test_real_requirement_chain_accounts_for_survival_"
            "litter_and_single_service"
        ),
    ),
    ("app/simulation/backward_planner.py", "birth_month"): (
        (
            "tests/test_mutation38_simulation_backward_biology_instructio"
            "ns_v3.py::test_real_class_window_dates_the_birth_that_reache"
            "s_mid_class_at_sale"
        ),
    ),
    ("app/simulation/backward_planner.py", "build_backward_plan"): (
        (
            "tests/test_mutation38_backward_plan_native_story_v2.py::test"
            "_actual_first_month_breeding_instruction_is_not_a_missed_dea"
            "dline"
        ),
        (
            "tests/test_mutation38_backward_plan_native_story_v2.py::test"
            "_actual_purchase_plan_reconciles_stage_stock_and_dated_biolo"
            "gical_advice"
        ),
        (
            "tests/test_mutation38_backward_plan_native_story_v2.py::test"
            "_dated_twenty_year_endpoint_is_supported_and_next_month_is_r"
            "ejected"
        ),
        (
            "tests/test_mutation38_backward_plan_native_story_v2.py::test"
            "_native_empty_target_list_has_its_controlled_disposition"
        ),
    ),
    ("app/simulation/backward_planner.py", "month_label"): (
        (
            "tests/test_mutation38_simulation_backward_calendar_completio"
            "n.py::test_actual_target_month_offset_matches_one_based_cale"
            "ndar"
        ),
    ),
    ("app/simulation/backward_planner.py", "month_offset"): (
        (
            "tests/test_mutation38_simulation_backward_calendar_completio"
            "n.py::test_actual_target_month_offset_matches_one_based_cale"
            "ndar"
        ),
    ),
    ("app/simulation/backward_planner.py", "parse_year_month"): (
        (
            "tests/test_mutation38_simulation_backward_calendar_completio"
            "n.py::test_real_planner_calendar_endpoint_parses"
        ),
    ),
    ("app/simulation/daily_ops.py", "__init__"): (
        (
            "tests/test_mutation38_simulation_daily_biological_calendar_v"
            "2.py::test_native_resting_clock_and_full_sire_capacity_recor"
            "d_every_waiting_doe"
        ),
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_annual_hazards_compound_to_the_configured_ad"
            "ult_and_grower_loss"
        ),
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_daily_records_conserve_actual_animals_tasks_"
            "and_transition_provenance"
        ),
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_pregnancy_milestones_and_later_service_match"
            "_species_calendar"
        ),
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_standing_head_day_cap_accepts_exact_capacity"
            "_and_rejects_one_less"
        ),
    ),
    ("app/simulation/daily_ops.py", "_age_culls"): (
        (
            "tests/test_mutation38_simulation_daily_biological_calendar_v"
            "2.py::test_native_policy_age_equality_culls_and_sells_the_ac"
            "tual_starting_head_on_day_one"
        ),
    ),
    ("app/simulation/daily_ops.py", "_breeding"): (
        (
            "tests/test_mutation38_simulation_daily_biological_calendar_v"
            "2.py::test_native_heat_wait_does_not_hold_back_a_different_d"
            "oe_reaching_breeding_age"
        ),
        (
            "tests/test_mutation38_simulation_daily_biological_calendar_v"
            "2.py::test_native_immature_starters_do_not_block_the_other_m"
            "ature_sire_or_doe"
        ),
        (
            "tests/test_mutation38_simulation_daily_biological_calendar_v"
            "2.py::test_native_resting_clock_and_full_sire_capacity_recor"
            "d_every_waiting_doe"
        ),
        (
            "tests/test_mutation38_simulation_daily_biological_calendar_v"
            "3.py::test_native_resting_clock_and_full_sire_capacity_recor"
            "d_every_waiting_doe"
        ),
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_daily_records_conserve_actual_animals_tasks_"
            "and_transition_provenance"
        ),
    ),
    ("app/simulation/daily_ops.py", "_build_explanations"): (
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_daily_records_conserve_actual_animals_tasks_"
            "and_transition_provenance"
        ),
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_standing_head_day_cap_accepts_exact_capacity"
            "_and_rejects_one_less"
        ),
        (
            "tests/test_mutation38_simulation_daily_operator_explanations"
            "_v6.py::test_last_day_sales_are_reflected_in_actual_final_he"
            "ad_and_building_explanations"
        ),
    ),
    ("app/simulation/daily_ops.py", "_build_notes"): (
        (
            "tests/test_mutation38_simulation_daily_operator_explanations"
            "_v6.py::test_last_day_sales_are_reflected_in_actual_final_he"
            "ad_and_building_explanations"
        ),
        (
            "tests/test_mutation38_simulation_daily_sire_availability_v1."
            "py::test_real_starter_sire_advice_agrees_with_sex_age_and_su"
            "pported_bucket"
        ),
    ),
    ("app/simulation/daily_ops.py", "_build_result"): (
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_daily_records_conserve_actual_animals_tasks_"
            "and_transition_provenance"
        ),
    ),
    ("app/simulation/daily_ops.py", "_check_herd"): (
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_coherent_animal_sex_and_bucket_state_is_admitted"
        ),
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_last_quarantine_day_before_protocol_release_is_admitt"
            "ed"
        ),
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_real_species_service_phase_endpoints_are_admitted"
        ),
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_service_day_outside_its_actual_bucket_phase_is_reject"
            "ed"
        ),
    ),
    ("app/simulation/daily_ops.py", "_check_sex_and_state"): (
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_animal_tag_supported_endpoints_are_preserved"
        ),
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_coherent_animal_sex_and_bucket_state_is_admitted"
        ),
    ),
    ("app/simulation/daily_ops.py", "_date"): (
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_daily_records_conserve_actual_animals_tasks_"
            "and_transition_provenance"
        ),
    ),
    ("app/simulation/daily_ops.py", "_dob_date"): (
        (
            "tests/test_mutation38_simulation_daily_recipe_calendar_v1.py"
            "::test_real_male_grower_recipe_changes_on_its_operational_ca"
            "lendar_boundary"
        ),
    ),
    ("app/simulation/daily_ops.py", "_draw_litter"): (
        (
            "tests/test_mutation38_simulation_daily_seeded_probability_bo"
            "undaries_v1.py::test_equal_litter_cdf_probability_selects_th"
            "e_next_supported_litter_size"
        ),
    ),
    ("app/simulation/daily_ops.py", "_exit"): (
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_daily_records_conserve_actual_animals_tasks_"
            "and_transition_provenance"
        ),
    ),
    ("app/simulation/daily_ops.py", "_feed_lines"): (
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_daily_records_conserve_actual_animals_tasks_"
            "and_transition_provenance"
        ),
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_feed_deliveries_follow_shift_occupants_and_p"
            "repared_stock_conserves_mass"
        ),
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v4."
            "py::test_native_daily_records_conserve_actual_animals_tasks_"
            "and_transition_provenance"
        ),
        (
            "tests/test_mutation38_simulation_daily_recipe_calendar_v1.py"
            "::test_real_male_grower_recipe_changes_on_its_operational_ca"
            "lendar_boundary"
        ),
    ),
    ("app/simulation/daily_ops.py", "_feed_round"): (
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_daily_records_conserve_actual_animals_tasks_"
            "and_transition_provenance"
        ),
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_feed_deliveries_follow_shift_occupants_and_p"
            "repared_stock_conserves_mass"
        ),
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v5."
            "py::test_native_daily_records_conserve_actual_animals_tasks_"
            "and_transition_provenance"
        ),
        (
            "tests/test_mutation38_simulation_daily_operator_explanations"
            "_v6.py::test_native_feed_tasks_identify_direct_roughage_and_"
            "the_actual_declared_shift_share"
        ),
    ),
    ("app/simulation/daily_ops.py", "_gestation_duties"): (
        (
            "tests/test_mutation38_simulation_daily_biological_calendar_v"
            "2.py::test_native_abortion_of_the_first_doe_does_not_skip_an"
            "other_actual_pregnancy"
        ),
        (
            "tests/test_mutation38_simulation_daily_biological_calendar_v"
            "2.py::test_native_phase_probability_stream_does_not_consume_"
            "an_abortion_draw_on_the_due_day"
        ),
        (
            "tests/test_mutation38_simulation_daily_biological_calendar_v"
            "2.py::test_native_primary_and_booster_calendars_do_not_repea"
            "t_prearrival_doses"
        ),
        (
            "tests/test_mutation38_simulation_daily_seeded_probability_bo"
            "undaries_v1.py::test_equal_daily_abortion_probability_retain"
            "s_the_first_day_pregnancy"
        ),
    ),
    ("app/simulation/daily_ops.py", "_kidding"): (
        (
            "tests/test_mutation38_simulation_daily_biological_calendar_v"
            "2.py::test_native_stillbirths_complete_each_entire_litter_on"
            "_each_dams_own_due_day"
        ),
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_daily_records_conserve_actual_animals_tasks_"
            "and_transition_provenance"
        ),
        (
            "tests/test_mutation38_simulation_daily_seeded_probability_bo"
            "undaries_v1.py::test_equal_female_probability_produces_a_mal"
            "e_kid_in_the_native_seeded_run"
        ),
        (
            "tests/test_mutation38_simulation_daily_seeded_probability_bo"
            "undaries_v1.py::test_equal_stillbirth_probability_retains_th"
            "e_live_native_birth"
        ),
    ),
    ("app/simulation/daily_ops.py", "_litter_probabilities"): (
        (
            "tests/test_mutation38_simulation_daily_probability_mass.py::"
            "test_litter_probabilities_conserve_birth_mass_and_expected_k"
            "ids"
        ),
    ),
    ("app/simulation/daily_ops.py", "_mortality"): (
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v5."
            "py::test_supported_actual_native_complete_mortality_retains_"
            "exit_and_maternal_accounting"
        ),
        (
            "tests/test_mutation38_simulation_daily_seeded_probability_bo"
            "undaries_v1.py::test_equal_daily_mortality_probability_retai"
            "ns_the_first_day_animal"
        ),
    ),
    ("app/simulation/daily_ops.py", "_move"): (
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_daily_records_conserve_actual_animals_tasks_"
            "and_transition_provenance"
        ),
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v5."
            "py::test_actual_prepared_engine_rejects_a_move_with_the_wron"
            "g_lifecycle_context"
        ),
    ),
    ("app/simulation/daily_ops.py", "_pregnancy_check_duties"): (
        (
            "tests/test_mutation38_simulation_daily_biological_calendar_v"
            "2.py::test_native_waiting_scan_does_do_not_delay_another_doe"
            "s_due_scan_or_expected_date"
        ),
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_two_failed_services_follow_the_real_next_heat_and_c"
            "ull_policy"
        ),
        (
            "tests/test_mutation38_simulation_daily_seeded_probability_bo"
            "undaries_v1.py::test_equal_scan_probability_excludes_concept"
            "ion_in_the_native_seeded_run"
        ),
    ),
    ("app/simulation/daily_ops.py", "_realistic_start"): (
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_coherent_animal_sex_and_bucket_state_is_admitted"
        ),
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_engine_accepts_its_calendar_endpoints"
        ),
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_engine_rejects_a_calendar_outside_its_window_document"
            "ed_range"
        ),
    ),
    ("app/simulation/daily_ops.py", "_record"): (
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_daily_records_conserve_actual_animals_tasks_"
            "and_transition_provenance"
        ),
    ),
    ("app/simulation/daily_ops.py", "_sales"): (
        (
            "tests/test_mutation38_simulation_daily_biological_calendar_v"
            "2.py::test_native_policy_age_equality_culls_and_sells_the_ac"
            "tual_starting_head_on_day_one"
        ),
    ),
    ("app/simulation/daily_ops.py", "_weaning_and_postpartum"): (
        (
            "tests/test_mutation38_simulation_daily_biological_calendar_v"
            "2.py::test_native_live_kids_wean_individually_and_face_the_a"
            "ctual_postweaning_class"
        ),
        (
            "tests/test_mutation38_simulation_daily_biological_calendar_v"
            "3.py::test_native_live_kids_wean_individually_and_face_the_a"
            "ctual_postweaning_class"
        ),
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_daily_records_conserve_actual_animals_tasks_"
            "and_transition_provenance"
        ),
    ),
    ("app/simulation/daily_ops.py", "build_daily_ledger"): (
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_full_ledger_preserves_real_duties_buildings_"
            "and_animal_journey_history"
        ),
    ),
    ("app/simulation/daily_ops.py", "run"): (
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_daily_records_conserve_actual_animals_tasks_"
            "and_transition_provenance"
        ),
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_standing_head_day_cap_accepts_exact_capacity"
            "_and_rejects_one_less"
        ),
    ),
    ("app/simulation/defaults.py", "_semi_intensive_weights"): (
        (
            "tests/test_mutation38_simulation_system_curve_contracts.py::"
            "test_native_system_copy_preserves_birth_and_breed_scaled_fie"
            "ld_growth"
        ),
    ),
    ("app/simulation/engine.py", "_add_purchased_does"): (
        (
            "tests/test_mutation38_simulation_growth_forecast_release_v2."
            "py::test_real_scheduled_adult_purchases_with_the_latest_supp"
            "orted_age_finish_and_reconcile"
        ),
    ),
    ("app/simulation/engine.py", "_calendar_month_of"): (
        (
            "tests/test_mutation38_simulation_market_hold_calendar_v2.py:"
            ":test_recurring_july_sale_month_repeats_after_the_gregorian_"
            "year_wrap"
        ),
    ),
    ("app/simulation/engine.py", "_capitalize"): (
        (
            "tests/test_mutation38_simulation_finance_asset_ledger_v3.py:"
            ":test_actual_adult_sale_derecognizes_the_remaining_acquisiti"
            "on_basis"
        ),
        (
            "tests/test_mutation38_simulation_finance_asset_ledger_v3.py:"
            ":test_zero_physical_does_have_no_remaining_acquisition_accou"
            "nt_after_their_age_exit"
        ),
    ),
    ("app/simulation/engine.py", "_cull_does"): (
        (
            "tests/test_mutation38_simulation_breeding_policy_accounting_"
            "v2.py::test_rate_cull_removes_only_its_fraction_of_the_survi"
            "ving_acquisition_book_value"
        ),
    ),
    ("app/simulation/engine.py", "_dispose_fraction"): (
        (
            "tests/test_mutation38_simulation_finance_asset_ledger_v3.py:"
            ":test_actual_adult_sale_derecognizes_the_remaining_acquisiti"
            "on_basis"
        ),
    ),
    ("app/simulation/engine.py", "_draw"): (
        (
            "tests/test_mutation38_simulation_engine_native_contracts_v3."
            "py::test_native_pool_full_removal_conserves_head_and_can_emp"
            "ty_the_pool"
        ),
    ),
    ("app/simulation/engine.py", "_is_festival_month"): (
        (
            "tests/test_mutation38_simulation_market_hold_calendar_v2.py:"
            ":test_recurring_january_sale_month_is_enabled_by_its_first_c"
            "alendar_position"
        ),
        (
            "tests/test_mutation38_simulation_market_hold_calendar_v2.py:"
            ":test_recurring_july_sale_month_repeats_after_the_gregorian_"
            "year_wrap"
        ),
    ),
    ("app/simulation/engine.py", "_months_until_next_festival"): (
        (
            "tests/test_mutation38_simulation_market_hold_calendar_v2.py:"
            ":test_explicit_sale_calendar_admits_a_full_twelve_month_hold"
            "ing_window"
        ),
    ),
    ("app/simulation/engine.py", "_parity_weighted"): (
        (
            "tests/test_mutation38_simulation_reproductive_cohort_contrac"
            "ts.py::test_purchased_does_complete_with_one_service_bucket_"
            "and_no_sire"
        ),
        (
            "tests/test_mutation38_simulation_reproductive_cohort_contrac"
            "ts.py::test_shed_places_include_does_held_between_same_month"
            "_purchase_and_sale"
        ),
    ),
    ("app/simulation/engine.py", "_physical_head"): (
        (
            "tests/test_mutation38_simulation_reproductive_cohort_contrac"
            "ts.py::test_shed_places_include_does_held_between_same_month"
            "_purchase_and_sale"
        ),
    ),
    ("app/simulation/engine.py", "_run_core"): (
        (
            "tests/test_mutation38_simulation_adult_age_conservation.py::"
            "test_valid_doe_age_policy_completes_and_conserves_retained_s"
            "tock"
        ),
        (
            "tests/test_mutation38_simulation_adult_sale_and_late_cull_v3"
            ".py::test_actual_sire_rotation_admits_zero_proceeds_and_cons"
            "erves_the_disposed_head"
        ),
        (
            "tests/test_mutation38_simulation_adult_sale_and_late_cull_v3"
            ".py::test_foundation_year_rate_cull_counts_real_bought_does_"
            "still_settling"
        ),
        (
            "tests/test_mutation38_simulation_adult_sale_and_late_cull_v3"
            ".py::test_unavailable_adult_sale_returns_the_full_shortfall_"
            "without_value_or_head"
        ),
        (
            "tests/test_mutation38_simulation_annual_cash_accounting.py::"
            "test_a_real_trading_purchase_is_expensed_once_in_annual_oper"
            "ating_profit_and_cash"
        ),
        (
            "tests/test_mutation38_simulation_annual_cash_accounting.py::"
            "test_a_twelve_year_forecast_labels_every_annual_period_in_se"
            "quence"
        ),
        (
            "tests/test_mutation38_simulation_annual_cash_accounting.py::"
            "test_approved_dated_grant_is_a_cash_benefit_while_remaining_"
            "outside_operating_income"
        ),
        (
            "tests/test_mutation38_simulation_breeding_policy_accounting_"
            "v2.py::test_automatic_restaff_funds_sires_while_purchased_do"
            "es_are_still_settling"
        ),
        (
            "tests/test_mutation38_simulation_breeding_policy_accounting_"
            "v2.py::test_repeat_cull_proportional_basis_includes_the_stil"
            "l_settling_breeding_pool"
        ),
        (
            "tests/test_mutation38_simulation_cull_market_cash.py::test_a"
            "ctual_adult_sale_quote_and_cash_share_the_market_exposure"
        ),
        (
            "tests/test_mutation38_simulation_engine_native_contracts_v3."
            "py::test_native_core_default_vocabulary_completes_a_real_sch"
            "eduled_disposal"
        ),
        (
            "tests/test_mutation38_simulation_engine_native_contracts_v3."
            "py::test_valid_json_forecast_completes_and_conserves_every_s"
            "tarting_cohort"
        ),
        (
            "tests/test_mutation38_simulation_finance_asset_ledger_v3.py:"
            ":test_actual_adult_sale_derecognizes_the_remaining_acquisiti"
            "on_basis"
        ),
        (
            "tests/test_mutation38_simulation_finance_asset_ledger_v3.py:"
            ":test_an_unlisted_zero_award_has_a_controlled_native_policy_"
            "error"
        ),
        (
            "tests/test_mutation38_simulation_finance_asset_ledger_v3.py:"
            ":test_approved_two_half_award_preserves_first_and_exact_hori"
            "zon_cash_receipts"
        ),
        (
            "tests/test_mutation38_simulation_finance_asset_ledger_v3.py:"
            ":test_policy_estimate_discloses_real_unit_and_budget_without"
            "_inventing_receipts"
        ),
        (
            "tests/test_mutation38_simulation_finance_asset_ledger_v3.py:"
            ":test_written_off_facilities_stop_depreciating_after_their_s"
            "upported_lifetime"
        ),
        (
            "tests/test_mutation38_simulation_finance_asset_ledger_v3.py:"
            ":test_zero_rate_loan_repayment_and_terminal_balloon_conserve"
            "_the_real_principal"
        ),
        (
            "tests/test_mutation38_simulation_finance_default_cash_v2.py:"
            ":test_omitted_breeder_life_depreciates_bought_sire_over_five"
            "_asset_years"
        ),
        (
            "tests/test_mutation38_simulation_finance_default_cash_v2.py:"
            ":test_omitted_worker_capacity_books_the_documented_half_atte"
            "ndant_steps"
        ),
        (
            "tests/test_mutation38_simulation_funding_conservation.py::te"
            "st_upfront_loan_and_subsidy_preserve_nonnegative_actual_prom"
            "oter_equity"
        ),
        (
            "tests/test_mutation38_simulation_growth_forecast_release_v2."
            "py::test_real_purchased_does_remain_present_and_first_concei"
            "ve_after_the_whole_settling_period"
        ),
        (
            "tests/test_mutation38_simulation_metric_economic_facts_v4.py"
            "::test_interest_only_horizon_reports_no_operating_principal_"
            "repayment_year"
        ),
        (
            "tests/test_mutation38_simulation_milk_path_receipts_v2.py::t"
            "est_actual_native_milk_paths_scale_receipts_without_changing"
            "_the_breeding_cohorts"
        ),
        (
            "tests/test_mutation38_simulation_ordered_event_accounting.py"
            "::test_ordered_adult_events_respect_capitalization_and_expli"
            "cit_zero_price"
        ),
        (
            "tests/test_mutation38_simulation_ordered_event_accounting.py"
            "::test_ordered_sale_prices_festival_held_males_at_their_actu"
            "al_older_age"
        ),
        (
            "tests/test_mutation38_simulation_ordered_event_accounting.py"
            "::test_ordered_young_purchase_and_partial_sale_conserve_age_"
            "money_and_head"
        ),
        (
            "tests/test_mutation38_simulation_reproductive_cohort_contrac"
            "ts.py::test_mixed_foundation_finishes_existing_pregnancies_w"
            "ithout_rebreed_wait"
        ),
        (
            "tests/test_mutation38_simulation_reproductive_cohort_contrac"
            "ts.py::test_native_mortality_shock_doubles_loss_before_expos"
            "ure_conversion"
        ),
        (
            "tests/test_mutation38_simulation_reproductive_cohort_contrac"
            "ts.py::test_purchased_does_complete_with_one_service_bucket_"
            "and_no_sire"
        ),
        (
            "tests/test_mutation38_simulation_resource_flow_accounting_v2"
            ".py::test_constant_sire_ration_prices_actual_crop_and_shortf"
            "all_and_conserves_water"
        ),
        (
            "tests/test_mutation38_simulation_short_grower_chain_v3.py::t"
            "est_default_arrival_age_in_the_shortest_nonempty_female_grow"
            "er_chain_completes"
        ),
        (
            "tests/test_mutation38_simulation_unavailable_stock_quotes.py"
            "::test_unavailable_class_keeps_the_placement_quote_of_its_ac"
            "tual_fresh_stock"
        ),
        (
            "tests/test_mutation38_simulation_unlimited_service_completio"
            "n.py::test_zero_conception_unlimited_service_completes_and_c"
            "onserves_ready_stock"
        ),
    ),
    ("app/simulation/engine.py", "_scale"): (
        (
            "tests/test_mutation38_simulation_engine_native_contracts_v3."
            "py::test_native_pool_full_removal_conserves_head_and_can_emp"
            "ty_the_pool"
        ),
    ),
    ("app/simulation/engine.py", "irr"): (
        (
            "tests/test_mutation38_simulation_lazy_irr_contract.py::test_"
            "native_forecast_lazy_return_preserves_its_actual_cashflow_di"
            "sposition"
        ),
    ),
    ("app/simulation/engine.py", "monthly_mortality_rate"): (
        (
            "tests/test_mutation38_simulation_engine_native_contracts_v3."
            "py::test_native_mortality_conversions_preserve_the_stated_ex"
            "posure_period"
        ),
    ),
    ("app/simulation/engine.py", "phase_monthly_mortality_rate"): (
        (
            "tests/test_mutation38_simulation_engine_native_contracts_v3."
            "py::test_native_mortality_conversions_preserve_the_stated_ex"
            "posure_period"
        ),
    ),
    ("app/simulation/engine.py", "remove_fraction"): (
        (
            "tests/test_mutation38_simulation_finance_asset_ledger_v3.py:"
            ":test_native_fractional_disposal_conserves_remaining_acquisi"
            "tion_cost_and_book"
        ),
    ),
    ("app/simulation/engine.py", "revenue"): (
        (
            "tests/test_mutation38_simulation_milk_cash_conservation_v2.p"
            "y::test_real_surplus_milk_receipts_conserve_monthly_and_annu"
            "al_operating_cash"
        ),
    ),
    ("app/simulation/engine.py", "run_simulation"): (
        (
            "tests/test_mutation38_simulation_forecast_calendar_native_v2"
            ".py::test_actual_calendar_coverage_warning_tracks_the_last_i"
            "nclusive_forecast_month"
        ),
        (
            "tests/test_mutation38_simulation_forecast_calendar_native_v2"
            ".py::test_actual_explicit_legacy_calendar_reports_only_month"
            "s_inside_its_declared_window"
        ),
        (
            "tests/test_mutation38_simulation_native_optional_analysis.py"
            "::test_native_deterministic_run_omits_unrequested_optional_a"
            "nalyses"
        ),
        (
            "tests/test_mutation38_simulation_reproductive_narrative_cont"
            "racts.py::test_native_omitted_vocabulary_preserves_the_expli"
            "cit_goat_report"
        ),
    ),
    ("app/simulation/engine.py", "weight_at_age"): (
        (
            "tests/test_mutation38_simulation_engine_native_contracts_v3."
            "py::test_native_growth_reaches_each_configured_live_weight_a"
            "nchor"
        ),
    ),
    ("app/simulation/explain.py", "_active_festival_months"): (
        (
            "tests/test_mutation38_simulation_forecast_calendar_native_v2"
            ".py::test_actual_explicit_legacy_calendar_reports_only_month"
            "s_inside_its_declared_window"
        ),
    ),
    ("app/simulation/explain.py", "_litter_expectation_paragraphs"): (
        (
            "tests/test_mutation38_simulation_reproductive_narrative_cont"
            "racts.py::test_litter_narrative_uses_maiden_and_second_parit"
            "y_and_honest_multiple_bands"
        ),
    ),
    ("app/simulation/explain.py", "_male_counted"): (
        (
            "tests/test_mutation38_simulation_reproductive_narrative_cont"
            "racts.py::test_counted_sire_vocabulary_uses_singular_only_fo"
            "r_one_head"
        ),
    ),
    ("app/simulation/explain.py", "_operating_principal"): (
        (
            "tests/test_mutation38_simulation_metric_economic_facts_v4.py"
            "::test_interest_only_horizon_reports_no_operating_principal_"
            "repayment_year"
        ),
    ),
    ("app/simulation/explain.py", "_ranked"): (
        (
            "tests/test_mutation38_simulation_finance_report_contracts_v3"
            ".py::test_native_cost_ranking_preserves_every_actual_paid_li"
            "ne"
        ),
        (
            "tests/test_mutation38_simulation_finance_report_contracts_v3"
            ".py::test_trading_stock_cash_is_a_positive_operating_cost_in"
            "_the_report"
        ),
    ),
    ("app/simulation/explain.py", "_share"): (
        (
            "tests/test_mutation38_simulation_finance_report_contracts_v3"
            ".py::test_profitable_cash_report_discloses_established_retur"
            "ns_and_actual_payback"
        ),
    ),
    ("app/simulation/explain.py", "build_metric_explanations"): (
        (
            "tests/test_mutation38_simulation_metric_economic_facts_v4.py"
            "::test_approved_grant_metric_retains_actual_estimate_status_"
            "and_dated_cash"
        ),
        (
            "tests/test_mutation38_simulation_metric_economic_facts_v4.py"
            "::test_final_month_payback_attributes_the_real_sale_or_liqui"
            "dation"
        ),
        (
            "tests/test_mutation38_simulation_metric_economic_facts_v4.py"
            "::test_interest_only_horizon_reports_no_operating_principal_"
            "repayment_year"
        ),
        (
            "tests/test_mutation38_simulation_metric_economic_facts_v4.py"
            "::test_native_default_explanation_knows_an_actual_completed_"
            "unsuccessful_price_search"
        ),
        (
            "tests/test_mutation38_simulation_metric_economic_facts_v4.py"
            "::test_the_first_real_emi_in_a_partial_year_remains_one_oper"
            "ating_repayment_year"
        ),
        (
            "tests/test_mutation38_simulation_metric_economic_facts_v4.py"
            "::test_zero_assumed_meat_price_reports_the_real_required_pri"
            "ce_without_a_percentage"
        ),
    ),
    ("app/simulation/explain.py", "build_narrative_report"): (
        (
            "tests/test_mutation38_simulation_finance_report_contracts_v3"
            ".py::test_profitable_cash_report_discloses_established_retur"
            "ns_and_actual_payback"
        ),
        (
            "tests/test_mutation38_simulation_finance_report_contracts_v3"
            ".py::test_reports_keep_the_actual_custom_and_declared_nlm_re"
            "ceipt_timing_distinct"
        ),
        (
            "tests/test_mutation38_simulation_finance_report_contracts_v3"
            ".py::test_trading_stock_cash_is_a_positive_operating_cost_in"
            "_the_report"
        ),
        (
            "tests/test_mutation38_simulation_finance_report_contracts_v3"
            ".py::test_unpaid_family_work_discloses_the_same_actual_adult"
            "_attendance_cost"
        ),
        (
            "tests/test_mutation38_simulation_finance_report_contracts_v3"
            ".py::test_zero_doe_and_zero_breeder_acquisition_reports_no_f"
            "abricated_labour_or_purchase"
        ),
        (
            "tests/test_mutation38_uncertainty_geometry_narrative_v5.py::"
            "test_real_risk_report_preserves_available_interval_endpoints"
            "_and_singleton_absence"
        ),
        (
            "tests/test_mutation38_uncertainty_geometry_narrative_v5.py::"
            "test_valid_native_result_with_one_unknown_sampling_field_omi"
            "ts_confidence_prose"
        ),
        (
            "tests/test_mutation38_uncertainty_geometry_narrative_v5.py::"
            "test_zero_event_forecast_does_not_claim_configured_disasters"
        ),
    ),
    ("app/simulation/feed.py", "class_feed"): (
        (
            "tests/test_mutation38_simulation_resource_flow_accounting_v2"
            ".py::test_constant_sire_ration_prices_actual_crop_and_shortf"
            "all_and_conserves_water"
        ),
    ),
    ("app/simulation/finance.py", "amortization_schedule"): (
        (
            "tests/test_mutation38_financial_math_native_contracts_v1.py:"
            ":test_native_annuity_helper_preserves_equated_payments_at_a_"
            "positive_high_rate"
        ),
        (
            "tests/test_mutation38_financial_math_native_contracts_v1.py:"
            ":test_zero_interest_schedule_conserves_principal_without_int"
            "erest"
        ),
    ),
    ("app/simulation/finance.py", "mirr"): (
        (
            "tests/test_mutation38_financial_math_native_contracts_v1.py:"
            ":test_mirr_reports_the_defined_return_or_an_undefined_projec"
            "t"
        ),
    ),
    ("app/simulation/finance.py", "monthly_emi"): (
        (
            "tests/test_mutation38_finance_signed_zero_payment_wire_contr"
            "act.py::test_public_zero_loan_sign_preserves_scheduled_payme"
            "nt_wire_value"
        ),
        (
            "tests/test_mutation38_financial_math_native_contracts_v1.py:"
            ":test_no_repayment_term_has_no_monthly_instalment"
        ),
    ),
    ("app/simulation/market.py", "bakrid_festival_months"): (
        (
            "tests/test_mutation38_simulation_market_release_calendar_v2."
            "py::test_release_festival_months_follow_the_actual_one_based"
            "_calendar_window"
        ),
    ),
    ("app/simulation/market.py", "bakrid_occurrences"): (
        (
            "tests/test_mutation38_simulation_market_release_calendar_v2."
            "py::test_public_release_advisory_dates_and_strictly_after_bo"
            "undaries_remain_complete"
        ),
    ),
    ("app/simulation/market.py", "cultivated_green_supply_kg_dm_for_month"): (
        (
            "tests/test_mutation38_simulation_resource_flow_accounting_v2"
            ".py::test_constant_sire_ration_prices_actual_crop_and_shortf"
            "all_and_conserves_water"
        ),
    ),
    ("app/simulation/montecarlo.py", "_annual_ar1_factors"): (
        (
            "tests/test_mutation38_monte_carlo_native_contracts_v5.py::te"
            "st_annual_factors_use_one_real_independent_innovation_per_pr"
            "ojection_year"
        ),
    ),
    ("app/simulation/montecarlo.py", "_apply_annual_price_variation"): (
        (
            "tests/test_mutation38_monte_carlo_native_contracts_v5.py::te"
            "st_disabled_meat_risk_retains_the_later_enabled_annual_feed_"
            "process"
        ),
    ),
    ("app/simulation/montecarlo.py", "_bootstrap_percentile_ci"): (
        (
            "tests/test_mutation38_monte_carlo_native_contracts_v5.py::te"
            "st_two_genuine_outcomes_have_a_bootstrap_sampling_interval"
        ),
    ),
    ("app/simulation/montecarlo.py", "_correlated_draws"): (
        (
            "tests/test_mutation38_monte_carlo_native_contracts_v5.py::te"
            "st_zero_correlation_recovers_the_real_gaussian_triangular_ma"
            "rginals"
        ),
        (
            "tests/test_mutation38_uncertainty_geometry_narrative_v5.py::"
            "test_real_correlated_samples_retain_configured_triangular_ma"
            "rginal_moments"
        ),
    ),
    ("app/simulation/montecarlo.py", "_histogram"): (
        (
            "tests/test_mutation38_monte_carlo_native_contracts_v5.py::te"
            "st_real_debt_free_loss_forecast_reports_measurable_risk_and_"
            "optional_sampling"
        ),
        (
            "tests/test_mutation38_monte_carlo_native_contracts_v5.py::te"
            "st_subnormal_cash_histogram_retains_all_observations_and_bin"
            "_geometry"
        ),
    ),
    ("app/simulation/montecarlo.py", "_pct_label"): (
        (
            "tests/test_mutation38_uncertainty_geometry_narrative_v5.py::"
            "test_fully_admitted_conception_ceiling_retains_a_valid_low_s"
            "ensitivity_variant"
        ),
    ),
    ("app/simulation/montecarlo.py", "_sensitivity_cases"): (
        (
            "tests/test_mutation38_uncertainty_geometry_narrative_v5.py::"
            "test_admitted_sale_age_sensitivity_retains_feasible_schedule"
            "d_arrivals_and_ceiling"
        ),
    ),
    ("app/simulation/montecarlo.py", "percentile"): (
        (
            "tests/test_mutation38_monte_carlo_native_contracts_v5.py::te"
            "st_linear_percentiles_preserve_actual_two_outcome_cash_endpo"
            "ints"
        ),
        (
            "tests/test_mutation38_monte_carlo_native_contracts_v5.py::te"
            "st_two_genuine_outcomes_have_a_bootstrap_sampling_interval"
        ),
    ),
    ("app/simulation/montecarlo.py", "run_monte_carlo"): (
        (
            "tests/test_mutation38_monte_carlo_native_contracts_v5.py::te"
            "st_real_debt_free_loss_forecast_reports_measurable_risk_and_"
            "optional_sampling"
        ),
        (
            "tests/test_mutation38_uncertainty_geometry_narrative_v5.py::"
            "test_real_mixed_profit_and_loss_runs_report_the_binomial_sam"
            "pling_error"
        ),
        (
            "tests/test_mutation38_uncertainty_single_repaying_dscr_v2.py"
            "::test_actual_unproductive_investment_distinguishes_no_debt_"
            "from_repaying_debt"
        ),
    ),
    ("app/simulation/optimization.py", "_candidate_from_core"): (
        (
            "tests/test_mutation38_simulation_optimizer_helper_completion"
            "_v2.py::test_actual_equity_funded_baseline_has_no_debt_const"
            "raint_or_missing_ceiling_error"
        ),
    ),
    ("app/simulation/optimization.py", "_linspace"): (
        (
            "tests/test_mutation38_simulation_optimizer_helper_completion"
            "_v2.py::test_valid_herd_size_axis_completes"
        ),
    ),
    ("app/simulation/optimization.py", "_sample_evenly"): (
        (
            "tests/test_mutation38_simulation_optimizer_helper_completion"
            "_v2.py::test_sampling_a_valid_decision_axis_completes"
        ),
    ),
    ("app/simulation/optimization.py", "_unique_bounded"): (
        (
            "tests/test_mutation38_simulation_optimizer_helper_completion"
            "_v2.py::test_bounded_financing_decisions_complete_and_keep_u"
            "nique_policies"
        ),
    ),
    ("app/simulation/optimization.py", "run_optimization"): (
        (
            "tests/test_mutation38_optimizer_native_policy_boundaries_v2."
            "py::test_optimizer_covers_legal_adjacent_policy_boundaries_f"
            "or_both_scaled_herds"
        ),
        (
            "tests/test_mutation38_optimizer_native_policy_boundaries_v2."
            "py::test_optimizer_debt_search_preserves_valid_total_financi"
            "ng_shares"
        ),
        (
            "tests/test_mutation38_optimizer_native_policy_boundaries_v2."
            "py::test_sale_age_ceiling_does_not_generate_invalid_optimize"
            "r_candidates"
        ),
        (
            "tests/test_mutation38_optimizer_native_policy_boundaries_v2."
            "py::test_zero_doe_optimizer_does_not_recommend_an_unneeded_f"
            "oundation_buck"
        ),
    ),
    ("app/simulation/planner.py", "_check_purchase_event_budget"): (
        (
            "tests/test_mutation38_planner_native_remaining_boundaries_v1"
            ".py::test_standalone_purchase_document_has_no_phantom_reserv"
            "ed_event"
        ),
        (
            "tests/test_mutation38_planner_native_risk_completion_v4.py::"
            "test_native_chunked_purchases_fill_the_public_document_budge"
            "t_without_a_ghost_reservation"
        ),
    ),
    ("app/simulation/planner.py", "_class_entry_age"): (
        (
            "tests/test_mutation38_planner_native_remaining_boundaries_v1"
            ".py::test_impossible_early_sales_report_class_specific_suppl"
            "y_calendar"
        ),
    ),
    ("app/simulation/planner.py", "_class_max_age"): (
        (
            "tests/test_mutation38_planner_class_age_admission_v1.py::tes"
            "t_planner_maximum_class_age_is_admitted_by_the_current_publi"
            "c_chain"
        ),
        (
            "tests/test_mutation38_simulation_backward_biology_instructio"
            "ns_v3.py::test_real_class_window_dates_the_birth_that_reache"
            "s_mid_class_at_sale"
        ),
    ),
    ("app/simulation/planner.py", "_marginal_kids_per_doe"): (
        (
            "tests/test_mutation38_planner_native_risk_completion_v4.py::"
            "test_real_first_litter_yield_preserves_the_requested_sex"
        ),
        (
            "tests/test_mutation38_planner_release_purchase_precision_v1."
            "py::test_current_release_retains_whole_animal_purchase_decis"
            "ion_at_valid_cull_boundary"
        ),
        (
            "tests/test_mutation38_simulation_forward_cohort_planning_v4."
            "py::test_purchased_doe_yield_counts_only_live_in_window_offs"
            "pring_and_retained_generation"
        ),
    ),
    ("app/simulation/planner.py", "_match_target_fills"): (
        (
            "tests/test_mutation38_planner_native_risk_completion_v4.py::"
            "test_actual_partial_sale_risk_respects_the_documented_half_h"
            "ead_tolerance"
        ),
        (
            "tests/test_mutation38_planner_native_risk_completion_v4.py::"
            "test_public_unfilled_sale_revenue_keeps_its_canonical_zero_w"
            "ire_value"
        ),
        (
            "tests/test_mutation38_simulation_forward_cohort_planning_v4."
            "py::test_real_out_of_order_same_month_sales_preserve_target_"
            "order_and_partial_fill_money"
        ),
    ),
    ("app/simulation/planner.py", "_plan_assumptions"): (
        (
            "tests/test_mutation38_simulation_forward_cohort_planning_v4."
            "py::test_real_out_of_order_same_month_sales_preserve_target_"
            "order_and_partial_fill_money"
        ),
    ),
    ("app/simulation/planner.py", "_projected_purchase_events"): (
        (
            "tests/test_mutation38_simulation_forward_cohort_planning_v4."
            "py::test_gap_closing_buys_the_two_does_whose_actual_kids_fil"
            "l_the_sale"
        ),
    ),
    ("app/simulation/planner.py", "_purchase_month_for"): (
        (
            "tests/test_mutation38_planner_native_risk_completion_v4.py::"
            "test_early_target_purchase_deadline_never_precedes_the_plan"
        ),
        (
            "tests/test_mutation38_simulation_forward_cohort_planning_v4."
            "py::test_purchase_deadline_preserves_class_age_gestation_and"
            "_one_month_slack"
        ),
    ),
    ("app/simulation/planner.py", "_purchases_from"): (
        (
            "tests/test_mutation38_planner_native_risk_completion_v4.py::"
            "test_native_chunked_purchases_fill_the_public_document_budge"
            "t_without_a_ghost_reservation"
        ),
    ),
    ("app/simulation/planner.py", "_survival_to_event_age"): (
        (
            "tests/test_mutation38_simulation_forward_cohort_planning_v4."
            "py::test_sale_event_survival_accounts_for_each_completed_cla"
            "ss_exposure"
        ),
    ),
    ("app/simulation/planner.py", "build_plan_report"): (
        (
            "tests/test_mutation38_planner_native_remaining_boundaries_v1"
            ".py::test_report_omits_unrequested_risk_simulation"
        ),
        (
            "tests/test_mutation38_planner_native_remaining_boundaries_v1"
            ".py::test_successfully_closed_plan_does_not_report_a_remaini"
            "ng_shortfall"
        ),
        (
            "tests/test_mutation38_planner_native_risk_completion_v4.py::"
            "test_one_real_purchased_doe_can_back_the_one_head_shortfall"
        ),
    ),
    ("app/simulation/planner.py", "close_gaps"): (
        (
            "tests/test_mutation38_planner_later_target_progress_v1.py::t"
            "est_unavailable_earlier_target_preserves_later_feasible_purc"
            "hase_progress"
        ),
        (
            "tests/test_mutation38_planner_native_remaining_boundaries_v1"
            ".py::test_a_filled_or_policy_excluded_target_does_not_block_"
            "the_next_target"
        ),
        (
            "tests/test_mutation38_planner_native_remaining_boundaries_v1"
            ".py::test_retained_purchases_leave_one_real_event_slot_for_g"
            "ap_closing"
        ),
        (
            "tests/test_mutation38_planner_native_risk_completion_v4.py::"
            "test_already_filled_target_does_not_hide_a_later_adult_polic"
            "y_shortfall"
        ),
        (
            "tests/test_mutation38_planner_native_risk_completion_v4.py::"
            "test_no_actual_sire_rolls_back_one_failed_round_and_reports_"
            "it_once"
        ),
        (
            "tests/test_mutation38_planner_native_risk_completion_v4.py::"
            "test_no_conception_retains_the_real_shortfall_without_dividi"
            "ng_by_zero"
        ),
        (
            "tests/test_mutation38_planner_native_risk_completion_v4.py::"
            "test_one_real_purchased_doe_can_back_the_one_head_shortfall"
        ),
        (
            "tests/test_mutation38_planner_native_risk_completion_v4.py::"
            "test_real_unbackable_adult_sale_has_only_its_policy_explanat"
            "ion"
        ),
        (
            "tests/test_mutation38_planner_native_zero_progress_budget_v1"
            ".py::test_supported_native_event_document_discards_a_zero_yi"
            "eld_purchase_round"
        ),
        (
            "tests/test_mutation38_planner_zero_yield_peer_v3.py::test_al"
            "l_male_births_leave_female_target_unmet_without_blocking_mal"
            "e_sale"
        ),
    ),
    ("app/simulation/planner.py", "plan_probabilities"): (
        (
            "tests/test_mutation38_planner_native_remaining_boundaries_v1"
            ".py::test_one_of_four_heads_does_not_meet_eighty_percent_sal"
            "e_target"
        ),
        (
            "tests/test_mutation38_planner_native_risk_completion_v4.py::"
            "test_actual_partial_sale_risk_respects_the_documented_half_h"
            "ead_tolerance"
        ),
        (
            "tests/test_mutation38_planner_native_risk_completion_v4.py::"
            "test_current_release_native_seeded_risk_replays_preserve_pub"
            "lic_frequencies"
        ),
        (
            "tests/test_mutation38_planner_native_risk_completion_v4.py::"
            "test_native_zero_risk_replays_have_finite_zero_frequencies"
        ),
    ),
    ("app/simulation/shocks.py", "neutral"): (
        (
            "tests/test_mutation38_uncertainty_geometry_narrative_v5.py::"
            "test_public_uncertainty_document_rejects_a_missing_or_extra_"
            "histogram_component"
        ),
    ),
    ("app/simulation/snapshot.py", "herd_cohorts"): (
        (
            "tests/test_mutation38_simulation_growth_forecast_release_v2."
            "py::test_herd_snapshot_unknown_ages_and_the_omitted_sire_bou"
            "ndary_retain_each_actual_head"
        ),
    ),
    ("app/utils.py", "allocate_money"): (
        (
            "tests/test_mutation38_livestock_finance_native_boundaries.py"
            "::test_native_zero_share_count_reports_the_declared_value_er"
            "ror"
        ),
    ),
}

CLASS_ORACLES = {
    ("app/models/species.py", "SpeciesProfile"): (
        (
            "tests/test_mutation38_livestock_species_constructor_defaults"
            ".py::test_original_profile_constructor_retains_the_documente"
            "d_additive_husbandry_defaults"
        ),
    ),
    ("app/schemas/animals.py", "AnimalOut"): (
        (
            "tests/test_mutation38_livestock_output_attribute_contracts.p"
            "y::test_output_schema_accepts_real_rows_and_matches_the_publ"
            "ic_profile"
        ),
    ),
    ("app/schemas/animals.py", "BucketMoveOut"): (
        (
            "tests/test_mutation38_livestock_output_attribute_contracts.p"
            "y::test_output_schema_accepts_real_rows_and_matches_the_publ"
            "ic_profile"
        ),
    ),
    ("app/schemas/animals.py", "StatusChangeIn"): (
        (
            "tests/test_mutation38_status_schema_contracts.py::test_statu"
            "s_text_accepts_the_wire_limit_and_rejects_one_more_character"
            "[buyer]"
        ),
        (
            "tests/test_mutation38_status_schema_contracts.py::test_statu"
            "s_text_accepts_the_wire_limit_and_rejects_one_more_character"
            "[mortality-cause]"
        ),
        (
            "tests/test_mutation38_status_schema_contracts.py::test_statu"
            "s_text_accepts_the_wire_limit_and_rejects_one_more_character"
            "[notes]"
        ),
        (
            "tests/test_mutation38_status_schema_contracts.py::test_statu"
            "s_text_accepts_the_wire_limit_and_rejects_one_more_character"
            "[suspected-disease]"
        ),
        (
            "tests/test_mutation38_status_schema_contracts.py::test_suppo"
            "rted_status_payloads_validate_and_preserve_their_facts[price"
            "d-sale]"
        ),
    ),
    ("app/schemas/breeding.py", "BreedingCandidateOut"): (
        ("tests/test_contract_drift.py::test_committed_openapi_json_matches_the_live_schema"),
    ),
    ("app/schemas/breeding.py", "BreedingCreateIn"): (
        ("tests/test_contract_drift.py::test_committed_openapi_json_matches_the_live_schema"),
        (
            "tests/test_mutation38_breeding_schema_boundaries.py::test_se"
            "men_identity_bound_distinguishes_input_shape_from_goat_proto"
            "col_refusal"
        ),
    ),
    ("app/schemas/breeding.py", "BreedingRecordOut"): (
        (
            "tests/test_mutation38_breeding_read_and_output_contracts.py:"
            ":test_breeding_output_validates_a_real_committed_record_and_"
            "its_wire_facts"
        ),
    ),
    ("app/schemas/breeding.py", "PregnancyLossIn"): (
        (
            "tests/test_mutation38_breeding_read_and_output_contracts.py:"
            ":test_loss_notes_are_trimmed_before_the_4000_character_admis"
            "sion_limit"
        ),
    ),
    ("app/schemas/feeding.py", "FinishedFeedStockOut"): (
        (
            "tests/test_mutation38_finished_feed_schema_adapter.py::test_"
            "finished_feed_projection_matches_the_public_ready_stock"
        ),
    ),
    ("app/schemas/finance.py", "InsurancePolicyIn"): (
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_p"
            "olicy_input_preserves_the_declared_text_boundaries[insurer-m"
            "ax]"
        ),
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_p"
            "olicy_input_preserves_the_declared_text_boundaries[insurer-s"
            "ingle]"
        ),
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_p"
            "olicy_input_preserves_the_declared_text_boundaries[insurer-t"
            "oo-long]"
        ),
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_p"
            "olicy_input_preserves_the_declared_text_boundaries[notes-max"
            "]"
        ),
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_p"
            "olicy_input_preserves_the_declared_text_boundaries[notes-too"
            "-long]"
        ),
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_p"
            "olicy_input_preserves_the_declared_text_boundaries[number-ma"
            "x]"
        ),
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_p"
            "olicy_input_preserves_the_declared_text_boundaries[number-si"
            "ngle]"
        ),
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_p"
            "olicy_input_preserves_the_declared_text_boundaries[number-to"
            "o-long]"
        ),
    ),
    ("app/schemas/finance.py", "TransactionCorrectionIn"): (
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_l"
            "edger_correction_input_preserves_the_declared_narrative_boun"
            "daries[notes-max]"
        ),
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_l"
            "edger_correction_input_preserves_the_declared_narrative_boun"
            "daries[notes-too-long]"
        ),
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_l"
            "edger_correction_input_preserves_the_declared_narrative_boun"
            "daries[reason-max]"
        ),
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_l"
            "edger_correction_input_preserves_the_declared_narrative_boun"
            "daries[reason-min]"
        ),
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_l"
            "edger_correction_input_preserves_the_declared_narrative_boun"
            "daries[reason-too-long]"
        ),
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_l"
            "edger_correction_input_preserves_the_declared_narrative_boun"
            "daries[reason-too-short]"
        ),
    ),
    ("app/schemas/finance.py", "TransactionOut"): (
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_t"
            "ransaction_adapter_matches_actual_committed_ledger_wire_fact"
            "s"
        ),
    ),
    ("app/schemas/health.py", "HealthBulkTargetIn"): (
        (
            "tests/test_mutation38_health_schema_contracts.py::test_bulk_"
            "round_component_accepts_its_wire_limit_and_rejects_one_more"
        ),
    ),
    ("app/schemas/health.py", "HealthEventIn"): (
        (
            "tests/test_mutation38_health_schema_contracts.py::test_healt"
            "h_dates_accept_valid_equalities_and_inclusive_ceiling[manufa"
            "cture-expiry-same-day]"
        ),
        (
            "tests/test_mutation38_health_schema_contracts.py::test_healt"
            "h_dates_accept_valid_equalities_and_inclusive_ceiling[maximu"
            "m-next-dose-period]"
        ),
        (
            "tests/test_mutation38_health_schema_contracts.py::test_healt"
            "h_dates_accept_valid_equalities_and_inclusive_ceiling[maximu"
            "m-withdrawal-period]"
        ),
        (
            "tests/test_mutation38_health_schema_contracts.py::test_healt"
            "h_dates_accept_valid_equalities_and_inclusive_ceiling[validi"
            "ty-at-product-expiry]"
        ),
        (
            "tests/test_mutation38_health_schema_contracts.py::test_healt"
            "h_dates_accept_valid_equalities_and_inclusive_ceiling[zero-w"
            "ithdrawal-period]"
        ),
        (
            "tests/test_mutation38_health_schema_contracts.py::test_healt"
            "h_text_envelopes_accept_the_limit_and_reject_the_next_charac"
            "ter[administered_by-120]"
        ),
        (
            "tests/test_mutation38_health_schema_contracts.py::test_healt"
            "h_text_envelopes_accept_the_limit_and_reject_the_next_charac"
            "ter[certificate_number-120]"
        ),
        (
            "tests/test_mutation38_health_schema_contracts.py::test_healt"
            "h_text_envelopes_accept_the_limit_and_reject_the_next_charac"
            "ter[disease_target-120]"
        ),
        (
            "tests/test_mutation38_health_schema_contracts.py::test_healt"
            "h_text_envelopes_accept_the_limit_and_reject_the_next_charac"
            "ter[dose-60]"
        ),
        (
            "tests/test_mutation38_health_schema_contracts.py::test_healt"
            "h_text_envelopes_accept_the_limit_and_reject_the_next_charac"
            "ter[next_due_authority-120]"
        ),
        (
            "tests/test_mutation38_health_schema_contracts.py::test_healt"
            "h_text_envelopes_accept_the_limit_and_reject_the_next_charac"
            "ter[official_tag_number-80]"
        ),
        (
            "tests/test_mutation38_health_schema_contracts.py::test_healt"
            "h_text_envelopes_accept_the_limit_and_reject_the_next_charac"
            "ter[product_lot-120]"
        ),
        (
            "tests/test_mutation38_health_schema_contracts.py::test_healt"
            "h_text_envelopes_accept_the_limit_and_reject_the_next_charac"
            "ter[schedule_template_name-120]"
        ),
        (
            "tests/test_mutation38_health_schema_contracts.py::test_healt"
            "h_text_envelopes_accept_the_limit_and_reject_the_next_charac"
            "ter[vet_name-120]"
        ),
        (
            "tests/test_mutation38_health_schema_contracts.py::test_next_"
            "dose_requires_a_later_date_within_the_ten_year_ceiling[same-"
            "day-next-dose]"
        ),
    ),
    ("app/schemas/health.py", "HealthRoundTargetChangeIn"): (
        (
            "tests/test_mutation38_health_schema_contracts.py::test_round"
            "_target_changes_accept_valid_boundary_payloads[full-bucket-t"
            "rimmed-reason]"
        ),
        (
            "tests/test_mutation38_health_schema_contracts.py::test_round"
            "_target_changes_accept_valid_boundary_payloads[single-target"
            "-short-reason]"
        ),
        (
            "tests/test_mutation38_health_schema_contracts.py::test_round"
            "_target_changes_reject_invalid_envelopes[empty-reason]"
        ),
        (
            "tests/test_mutation38_health_schema_contracts.py::test_round"
            "_target_changes_reject_invalid_envelopes[empty-targets]"
        ),
    ),
    ("app/schemas/health.py", "MovementRestrictionClearIn"): (
        (
            "tests/test_mutation38_restriction_clear_schema.py::test_rest"
            "riction_clearance_accepts_valid_reference_boundaries[longest"
            "-reference]"
        ),
        (
            "tests/test_mutation38_restriction_clear_schema.py::test_rest"
            "riction_clearance_accepts_valid_reference_boundaries[shortes"
            "t-reference]"
        ),
        (
            "tests/test_mutation38_restriction_clear_schema.py::test_rest"
            "riction_clearance_accepts_valid_reference_boundaries[strippe"
            "d-reference]"
        ),
        (
            "tests/test_mutation38_restriction_clear_schema.py::test_rest"
            "riction_clearance_rejects_missing_or_overlong_evidence[empty"
            "-reference]"
        ),
        (
            "tests/test_mutation38_restriction_clear_schema.py::test_rest"
            "riction_clearance_rejects_missing_or_overlong_evidence[overl"
            "ong-reference]"
        ),
    ),
    ("app/schemas/health.py", "ScheduleTemplateOut"): (
        (
            "tests/test_mutation38_health_schema_contracts.py::test_seede"
            "d_schedule_projection_preserves_the_native_attribute_adapter"
            "[seeded-vaccine]"
        ),
    ),
    ("app/schemas/kidding.py", "KidIn"): (
        (
            "tests/test_mutation38_livestock_kidding_wire.py::test_native"
            "_kid_identifier_accepts_fifty_characters_and_rejects_fifty_o"
            "ne"
        ),
        (
            "tests/test_mutation38_livestock_kidding_wire.py::test_native"
            "_kid_input_normalizes_identifier_whitespace"
        ),
    ),
    ("app/schemas/kidding.py", "KiddingCreateIn"): (
        (
            "tests/test_mutation38_livestock_kidding_wire.py::test_native"
            "_kidding_input_requires_at_least_one_kid"
        ),
    ),
    ("app/schemas/kidding.py", "KiddingRecordOut"): (
        (
            "tests/test_mutation38_livestock_kidding_wire.py::test_native"
            "_kidding_output_validates_real_eager_loaded_records"
        ),
    ),
    ("app/schemas/planner.py", "PlannerPlanCreateIn"): (
        (
            "tests/test_mutation38_saved_decision_schema_boundaries_v1.py"
            "::test_saved_document_name_json_admission"
        ),
        (
            "tests/test_mutation38_saved_decision_schema_boundaries_v1.py"
            "::test_saved_document_notes_json_admission"
        ),
        (
            "tests/test_mutation38_saved_decision_schema_boundaries_v1.py"
            "::test_saved_plan_target_batch_json_admission"
        ),
    ),
    ("app/schemas/planner.py", "PlannerPlanOut"): (
        (
            "tests/test_mutation38_saved_decision_schema_boundaries_v1.py"
            "::test_saved_document_openapi_marks_validity_default_for_cli"
            "ents"
        ),
    ),
    ("app/schemas/planner.py", "PlannerPlanUpdateIn"): (
        (
            "tests/test_mutation38_saved_decision_native_crud_v2.py::test"
            "_saved_decision_legacy_revision_bridge_allows_exactly_one_wr"
            "ite"
        ),
        (
            "tests/test_mutation38_saved_decision_schema_boundaries_v1.py"
            "::test_saved_document_name_json_admission"
        ),
        (
            "tests/test_mutation38_saved_decision_schema_boundaries_v1.py"
            "::test_saved_document_notes_json_admission"
        ),
        (
            "tests/test_mutation38_saved_decision_schema_boundaries_v1.py"
            "::test_saved_document_revision_zero_is_not_a_valid_write_tok"
            "en"
        ),
        (
            "tests/test_mutation38_saved_decision_schema_boundaries_v1.py"
            "::test_saved_plan_target_batch_json_admission"
        ),
    ),
    ("app/schemas/purchases.py", "PurchaseBatchIn"): (
        (
            "tests/test_mutation38_purchase_schema_boundaries.py::test_pu"
            "rchase_origin_market_accepts_120_characters_and_persists_the"
            "m"
        ),
        (
            "tests/test_mutation38_purchase_schema_boundaries.py::test_pu"
            "rchase_transport_accepts_a_recorded_zero_hour_journey"
        ),
    ),
    ("app/schemas/purchases.py", "PurchaseBatchOut"): (
        (
            "tests/test_mutation38_purchase_schema_boundaries.py::test_re"
            "tained_older_purchase_response_replays_zero_optional_occupan"
            "cy_counts"
        ),
    ),
    ("app/schemas/screening.py", "ScreeningBatchBucketProgressOut"): (
        (
            "tests/test_mutation38_screening_batch_contracts.py::test_wal"
            "kthrough_progress_conserves_actual_mixed_and_legacy_image_st"
            "ates"
        ),
    ),
    ("app/schemas/screening.py", "ScreeningBatchOut"): (
        (
            "tests/test_mutation38_screening_batch_contracts.py::test_wal"
            "kthrough_progress_conserves_actual_mixed_and_legacy_image_st"
            "ates"
        ),
    ),
    ("app/schemas/screening.py", "ScreeningCropOut"): (
        (
            "tests/test_mutation38_screening_detection_geometry.py::test_"
            "committed_model_generated_crop_supports_its_declared_native_"
            "wire_adapter"
        ),
    ),
    ("app/schemas/screening.py", "ScreeningImageRowOut"): (
        (
            "tests/test_mutation38_screening_optional_queue_count_default"
            "s.py::test_optional_public_row_counts_describe_the_real_empt"
            "y_review_queue"
        ),
    ),
    ("app/schemas/screening.py", "ScreeningProviderStatsOut"): (
        (
            "tests/test_mutation38_screening_scoreboard_export_contracts."
            "py::test_published_stats_contract_has_a_thirty_day_default_a"
            "nd_zero_count_defaults"
        ),
    ),
    ("app/schemas/screening.py", "ScreeningUploadIn"): (
        (
            "tests/test_mutation38_screening_batch_contracts.py::test_upl"
            "oad_filename_adapter_preserves_five_to_255_character_device_"
            "names[maximum]"
        ),
        (
            "tests/test_mutation38_screening_batch_contracts.py::test_upl"
            "oad_filename_adapter_preserves_five_to_255_character_device_"
            "names[overlong]"
        ),
        (
            "tests/test_mutation38_screening_batch_contracts.py::test_upl"
            "oad_filename_adapter_preserves_five_to_255_character_device_"
            "names[shortest]"
        ),
        (
            "tests/test_mutation38_screening_batch_contracts.py::test_upl"
            "oad_filename_adapter_preserves_five_to_255_character_device_"
            "names[trim-device-name]"
        ),
        (
            "tests/test_mutation38_screening_batch_contracts.py::test_upl"
            "oad_registration_metadata_keeps_the_literal_one_byte_to_25_m"
            "ib_band[empty]"
        ),
    ),
    ("app/schemas/simulation.py", "ScenarioCreateIn"): (
        (
            "tests/test_mutation38_saved_decision_schema_boundaries_v1.py"
            "::test_saved_document_name_json_admission"
        ),
        (
            "tests/test_mutation38_saved_decision_schema_boundaries_v1.py"
            "::test_saved_document_notes_json_admission"
        ),
    ),
    ("app/schemas/simulation.py", "ScenarioOut"): (
        (
            "tests/test_mutation38_saved_decision_schema_boundaries_v1.py"
            "::test_saved_document_openapi_marks_validity_default_for_cli"
            "ents"
        ),
    ),
    ("app/schemas/simulation.py", "ScenarioUpdateIn"): (
        (
            "tests/test_mutation38_saved_decision_schema_boundaries_v1.py"
            "::test_saved_document_name_json_admission"
        ),
        (
            "tests/test_mutation38_saved_decision_schema_boundaries_v1.py"
            "::test_saved_document_notes_json_admission"
        ),
        (
            "tests/test_mutation38_saved_decision_schema_boundaries_v1.py"
            "::test_saved_document_revision_zero_is_not_a_valid_write_tok"
            "en"
        ),
    ),
    ("app/schemas/tasks.py", "TaskCreateIn"): (
        (
            "tests/test_mutation38_task_schema_contracts.py::test_manual_"
            "duty_title_keeps_the_published_one_to_two_hundred_character_"
            "band[1-True]"
        ),
    ),
    ("app/schemas/tasks.py", "TaskOut"): (
        (
            "tests/test_mutation38_task_schema_contracts.py::test_retaine"
            "d_manual_duty_response_does_not_invent_a_verification_requir"
            "ement"
        ),
    ),
    ("app/schemas/tasks.py", "TaskSkipIn"): (
        (
            "tests/test_mutation38_task_schema_contracts.py::test_duty_au"
            "dit_reason_adapters_trim_and_bound_actual_human_input[blank-"
            "reason-skip]"
        ),
        (
            "tests/test_mutation38_task_schema_contracts.py::test_duty_au"
            "dit_reason_adapters_trim_and_bound_actual_human_input[max-re"
            "ason-skip]"
        ),
        (
            "tests/test_mutation38_task_schema_contracts.py::test_duty_au"
            "dit_reason_adapters_trim_and_bound_actual_human_input[overlo"
            "ng-reason-skip]"
        ),
        (
            "tests/test_mutation38_task_schema_contracts.py::test_duty_au"
            "dit_reason_adapters_trim_and_bound_actual_human_input[trim-h"
            "uman-reason-skip]"
        ),
    ),
    ("app/services/screening/detect.py", "_DetectedGoat"): (
        (
            "tests/test_mutation38_screening_detection_geometry.py::test_"
            "malformed_tuple_does_not_hide_the_following_real_goat[long]"
        ),
        (
            "tests/test_mutation38_screening_detection_geometry.py::test_"
            "malformed_tuple_does_not_hide_the_following_real_goat[short]"
        ),
    ),
    ("app/services/screening/gate.py", "GateObservation"): (
        (
            "tests/test_mutation38_screening_gate_contracts.py::test_nati"
            "ve_gate_observation_preserves_literal_text_bands[label-empty"
            "]"
        ),
        (
            "tests/test_mutation38_screening_gate_contracts.py::test_nati"
            "ve_gate_observation_preserves_literal_text_bands[label-first"
            "]"
        ),
        (
            "tests/test_mutation38_screening_gate_contracts.py::test_nati"
            "ve_gate_observation_preserves_literal_text_bands[label-maxim"
            "um]"
        ),
        (
            "tests/test_mutation38_screening_gate_contracts.py::test_nati"
            "ve_gate_observation_preserves_literal_text_bands[label-overl"
            "ong]"
        ),
        (
            "tests/test_mutation38_screening_gate_contracts.py::test_nati"
            "ve_gate_observation_preserves_literal_text_bands[note-maximu"
            "m]"
        ),
        (
            "tests/test_mutation38_screening_gate_contracts.py::test_nati"
            "ve_gate_observation_preserves_literal_text_bands[note-overlo"
            "ng]"
        ),
        (
            "tests/test_mutation38_screening_gate_contracts.py::test_nati"
            "ve_gate_observation_preserves_literal_text_bands[region-empt"
            "y]"
        ),
        (
            "tests/test_mutation38_screening_gate_contracts.py::test_nati"
            "ve_gate_observation_preserves_literal_text_bands[region-firs"
            "t]"
        ),
        (
            "tests/test_mutation38_screening_gate_contracts.py::test_nati"
            "ve_gate_observation_preserves_literal_text_bands[region-maxi"
            "mum]"
        ),
        (
            "tests/test_mutation38_screening_gate_contracts.py::test_nati"
            "ve_gate_observation_preserves_literal_text_bands[region-over"
            "long]"
        ),
        (
            "tests/test_mutation38_screening_gate_contracts.py::test_nati"
            "ve_gate_trims_device_text_and_defaults_to_assessable_quality"
        ),
    ),
    ("app/services/screening/gate.py", "GateResponse"): (
        (
            "tests/test_mutation38_screening_gate_contracts.py::test_nati"
            "ve_gate_trims_device_text_and_defaults_to_assessable_quality"
        ),
    ),
    ("app/services/screening/pipeline.py", "CycleSummary"): (
        (
            "tests/test_mutation38_screening_queue_native_contracts.py::t"
            "est_new_native_cycle_summary_has_no_phantom_claims"
        ),
    ),
    ("app/services/screening/specialists.py", "SpecialistCondition"): (
        (
            "tests/test_mutation38_screening_specialist_parser_contracts."
            "py::test_specialist_disease_guess_boundaries_preserve_valid_"
            "original_codes"
        ),
        (
            "tests/test_mutation38_screening_specialist_parser_contracts."
            "py::test_specialist_known_disease_note_preserves_the_declare"
            "d_boundary"
        ),
        (
            "tests/test_mutation38_screening_specialist_parser_contracts."
            "py::test_specialist_trims_provider_whitespace_before_vocabul"
            "ary_and_severity_validation"
        ),
    ),
    ("app/simulation/assumptions.py", "MetaAssumptions"): (
        (
            "tests/test_mutation38_saved_decision_schema_boundaries_v1.py"
            "::test_meta_horizon_json_admission"
        ),
    ),
    ("app/simulation/assumptions.py", "_Group"): (
        (
            "tests/test_mutation38_saved_decision_schema_boundaries_v1.py"
            "::test_meta_horizon_json_admission"
        ),
    ),
}

FIELD_ORACLES = {
    ("app/schemas/animals.py", "AnimalCreateIn", "historical_import_reason"): (
        (
            "tests/test_mutation38_livestock_schema_boundaries.py::test_e"
            "mpty_historical_reason_reports_the_offending_field"
        ),
        (
            "tests/test_mutation38_livestock_schema_boundaries.py::test_h"
            "istorical_import_reason_accepts_and_audits_its_exact_bounds"
        ),
        (
            "tests/test_mutation38_livestock_schema_boundaries.py::test_t"
            "oo_long_historical_import_reason_is_rejected_before_creating"
            "_an_animal"
        ),
    ),
    ("app/schemas/animals.py", "AnimalCreateIn", "tag_number"): (
        (
            "tests/test_mutation38_livestock_schema_boundaries.py::test_h"
            "istorical_animal_accepts_a_single_character_tag"
        ),
    ),
    ("app/schemas/animals.py", "AnimalOut", "days_in_current_bucket"): (
        (
            "tests/test_mutation38_livestock_schema_boundaries.py::test_r"
            "etained_older_animal_response_replays_missing_optional_facts"
            "_conservatively"
        ),
    ),
    ("app/schemas/animals.py", "AnimalOut", "is_breeding_ready"): (
        (
            "tests/test_mutation38_livestock_schema_boundaries.py::test_r"
            "etained_older_animal_response_replays_missing_optional_facts"
            "_conservatively"
        ),
    ),
    ("app/schemas/animals.py", "AnimalOut", "is_currently_pregnant"): (
        (
            "tests/test_mutation38_livestock_schema_boundaries.py::test_r"
            "etained_older_animal_response_replays_missing_optional_facts"
            "_conservatively"
        ),
    ),
    ("app/schemas/animals.py", "MoveIn", "reason"): (
        (
            "tests/test_mutation38_livestock_move_reason_boundaries.py::t"
            "est_move_reason_accepts_and_preserves_all_255_characters"
        ),
        (
            "tests/test_mutation38_livestock_move_reason_boundaries.py::t"
            "est_move_reason_rejects_256_characters_before_changing_histo"
            "ry"
        ),
    ),
    ("app/schemas/animals.py", "WeightIn", "notes"): (
        (
            "tests/test_mutation38_livestock_schema_boundaries.py::test_w"
            "eight_notes_accept_the_full_255_character_boundary"
        ),
        (
            "tests/test_mutation38_livestock_schema_boundaries.py::test_w"
            "eight_notes_reject_256_characters_before_the_database_write"
        ),
    ),
    ("app/schemas/ops_simulation.py", "DailyOpsRunIn", "animals"): (
        (
            "tests/test_mutation38_simulation_daily_input_envelope_v5.py:"
            ":test_empty_or_oversized_starting_herd_is_rejected"
        ),
        (
            "tests/test_mutation38_simulation_daily_input_envelope_v5.py:"
            ":test_run_and_engine_accept_the_supported_daily_window_endpo"
            "ints"
        ),
        (
            "tests/test_mutation38_simulation_daily_input_envelope_v5.py:"
            ":test_starting_herd_endpoint_keeps_every_unique_animal"
        ),
    ),
    ("app/schemas/ops_simulation.py", "DailyOpsRunIn", "horizon_days"): (
        (
            "tests/test_mutation38_simulation_daily_input_envelope_v5.py:"
            ":test_omitted_daily_window_and_seed_match_the_operator_form"
        ),
        (
            "tests/test_mutation38_simulation_daily_input_envelope_v5.py:"
            ":test_run_and_engine_accept_the_supported_daily_window_endpo"
            "ints"
        ),
        (
            "tests/test_mutation38_simulation_daily_input_envelope_v5.py:"
            ":test_run_and_engine_reject_days_outside_the_supported_daily"
            "_window"
        ),
    ),
    ("app/schemas/ops_simulation.py", "DailyOpsRunIn", "include_ledger"): (
        "tests/test_daily_ops_api.py::test_run_returns_day_by_day_simulation",
    ),
    ("app/schemas/ops_simulation.py", "DailyOpsRunIn", "seed"): (
        (
            "tests/test_mutation38_simulation_daily_input_envelope_v5.py:"
            ":test_omitted_daily_window_and_seed_match_the_operator_form"
        ),
    ),
    ("app/schemas/owner.py", "OwnerFarmBenchmarksOut", "farm_id"): (
        (
            "tests/test_mutation38_dashboard_native_contract_v4.py::test_"
            "actual_public_owner_dto_requires_a_positive_farm_identity"
        ),
    ),
    ("app/schemas/owner.py", "OwnerFarmOverviewOut", "farm_id"): (
        (
            "tests/test_mutation38_dashboard_native_contract_v4.py::test_"
            "actual_public_owner_dto_requires_a_positive_farm_identity"
        ),
    ),
    ("app/schemas/planner.py", "BackwardPlanIn", "risk_runs"): (
        (
            "tests/test_mutation38_planner_public_admission_boundaries_v2"
            ".py::test_normal_json_planner_boundaries"
        ),
        (
            "tests/test_mutation38_planner_public_admission_boundaries_v2"
            ".py::test_omitted_risk_replays_are_the_same_zero_risk_plan"
        ),
    ),
    ("app/schemas/planner.py", "BackwardPlanIn", "targets"): (
        (
            "tests/test_mutation38_planner_public_admission_boundaries_v2"
            ".py::test_normal_json_planner_boundaries"
        ),
    ),
    ("app/schemas/planner.py", "PlannerTargetIn", "count"): (
        (
            "tests/test_mutation38_planner_public_admission_boundaries_v2"
            ".py::test_normal_json_planner_boundaries"
        ),
    ),
    ("app/schemas/screening.py", "ScreeningUploadIn", "file_name"): (
        (
            "tests/test_mutation38_screening_public_filename_schema_v1.py"
            "::test_public_openapi_upload_filename_preserves_client_minim"
            "um"
        ),
    ),
    ("app/schemas/simulation.py", "RunIn", "monte_carlo"): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_normal_json_run_requires_explicit_opt_in_for_every_optio"
            "nal_analysis"
        ),
    ),
    ("app/schemas/simulation.py", "RunIn", "optimization"): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_normal_json_run_requires_explicit_opt_in_for_every_optio"
            "nal_analysis"
        ),
    ),
    ("app/schemas/simulation.py", "RunIn", "sensitivity"): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_normal_json_run_requires_explicit_opt_in_for_every_optio"
            "nal_analysis"
        ),
    ),
    ("app/schemas/summaries.py", "DashboardWeightOut", "model_config"): (
        (
            "tests/test_mutation38_dashboard_native_contract_v4.py::test_"
            "declared_summary_dtos_adapt_actual_native_records_without_ex"
            "tra_fields"
        ),
    ),
    ("app/schemas/summaries.py", "PurchaseQuarantineAnimalOut", "model_config"): (
        (
            "tests/test_mutation38_dashboard_native_contract_v4.py::test_"
            "declared_summary_dtos_adapt_actual_native_records_without_ex"
            "tra_fields"
        ),
    ),
    ("app/schemas/summaries.py", "QuarantineScheduleTaskOut", "model_config"): (
        (
            "tests/test_mutation38_dashboard_native_contract_v4.py::test_"
            "declared_summary_dtos_adapt_actual_native_records_without_ex"
            "tra_fields"
        ),
    ),
    (
        "app/simulation/assumptions.py",
        "CostsAssumptions",
        "breeding_stock_useful_life_months",
    ): (
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_out_of_range_cost_input_identifies_the_editor_field"
        ),
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_published_cost_editor_endpoints_are_admitted"
        ),
        (
            "tests/test_mutation38_simulation_finance_default_cash_v2.py:"
            ":test_omitted_breeder_life_depreciates_bought_sire_over_five"
            "_asset_years"
        ),
    ),
    (
        "app/simulation/assumptions.py",
        "CostsAssumptions",
        "equipment_useful_life_years",
    ): (
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_out_of_range_cost_input_identifies_the_editor_field"
        ),
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_published_cost_editor_endpoints_are_admitted"
        ),
        (
            "tests/test_mutation38_simulation_finance_default_cash_v2.py:"
            ":test_omitted_facility_lives_preserve_twenty_and_seven_year_"
            "asset_cash"
        ),
    ),
    (
        "app/simulation/assumptions.py",
        "CostsAssumptions",
        "labour_per_head_threshold",
    ): (
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_out_of_range_cost_input_identifies_the_editor_field"
        ),
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_published_cost_editor_endpoints_are_admitted"
        ),
        (
            "tests/test_mutation38_simulation_finance_default_cash_v2.py:"
            ":test_omitted_worker_capacity_books_the_documented_half_atte"
            "ndant_steps"
        ),
    ),
    ("app/simulation/assumptions.py", "CostsAssumptions", "planned_capacity_head"): (
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_user_approved_capacity_requires_an_actual_positive_suppli"
            "ed_plan"
        ),
    ),
    ("app/simulation/assumptions.py", "CostsAssumptions", "shed_useful_life_years"): (
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_out_of_range_cost_input_identifies_the_editor_field"
        ),
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_published_cost_editor_endpoints_are_admitted"
        ),
        (
            "tests/test_mutation38_simulation_finance_default_cash_v2.py:"
            ":test_omitted_facility_lives_preserve_twenty_and_seven_year_"
            "asset_cash"
        ),
    ),
    ("app/simulation/assumptions.py", "CullingAssumptions", "buck_doe_ratio"): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_json_integer_policy_retains_both_endpoints_and_rejects_o"
            "utside"
        ),
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_omitted_json_policy_preserves_the_documented_reference_u"
            "nit"
        ),
    ),
    ("app/simulation/assumptions.py", "CullingAssumptions", "buck_rotation_years"): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_json_integer_policy_retains_both_endpoints_and_rejects_o"
            "utside"
        ),
    ),
    ("app/simulation/assumptions.py", "CullingAssumptions", "max_doe_age_months"): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_json_integer_policy_retains_both_endpoints_and_rejects_o"
            "utside"
        ),
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_omitted_json_policy_preserves_the_documented_reference_u"
            "nit"
        ),
    ),
    (
        "app/simulation/assumptions.py",
        "FeedAssumptions",
        "monthly_concentrate_price_multipliers",
    ): (
        (
            "tests/test_mutation38_simulation_resource_calendar_contracts"
            ".py::test_feed_json_requires_one_observation_for_every_month"
            "_of_the_year"
        ),
    ),
    (
        "app/simulation/assumptions.py",
        "FeedAssumptions",
        "monthly_dry_price_multipliers",
    ): (
        (
            "tests/test_mutation38_simulation_resource_calendar_contracts"
            ".py::test_feed_json_requires_one_observation_for_every_month"
            "_of_the_year"
        ),
    ),
    (
        "app/simulation/assumptions.py",
        "FeedAssumptions",
        "monthly_fodder_yield_multipliers",
    ): (
        (
            "tests/test_mutation38_simulation_resource_calendar_contracts"
            ".py::test_feed_json_requires_one_observation_for_every_month"
            "_of_the_year"
        ),
    ),
    (
        "app/simulation/assumptions.py",
        "FeedAssumptions",
        "monthly_green_price_multipliers",
    ): (
        (
            "tests/test_mutation38_simulation_resource_calendar_contracts"
            ".py::test_feed_json_requires_one_observation_for_every_month"
            "_of_the_year"
        ),
    ),
    ("app/simulation/assumptions.py", "FinanceAssumptions", "loan_term_months"): (
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_out_of_range_finance_input_identifies_its_field"
        ),
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_published_finance_editor_endpoints_are_admitted"
        ),
    ),
    ("app/simulation/assumptions.py", "FinanceAssumptions", "moratorium_months"): (
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_out_of_range_finance_input_identifies_its_field"
        ),
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_published_finance_editor_endpoints_are_admitted"
        ),
        (
            "tests/test_mutation38_simulation_finance_default_cash_v2.py:"
            ":test_omitted_moratorium_starts_real_principal_repayment_aft"
            "er_twelve_months"
        ),
    ),
    ("app/simulation/assumptions.py", "FinanceAssumptions", "nlm_subsidy_receipts"): (
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_approved_award_can_equal_half_the_eligible_budget_and_pre"
            "serves_installments"
        ),
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_supplied_installments_conserve_award_halves_and_chronolog"
            "y"
        ),
    ),
    ("app/simulation/assumptions.py", "FinanceAssumptions", "nlm_unit_females"): (
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_unfunded_unit_configuration_is_not_an_approval"
        ),
    ),
    ("app/simulation/assumptions.py", "FinanceAssumptions", "nlm_unit_males"): (
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_unfunded_unit_configuration_is_not_an_approval"
        ),
    ),
    ("app/simulation/assumptions.py", "FinanceAssumptions", "tax_loss_carryforward"): (
        (
            "tests/test_mutation38_simulation_finance_default_cash_v2.py:"
            ":test_omitted_loss_carryforward_offsets_real_next_year_sale_"
            "profit"
        ),
    ),
    ("app/simulation/assumptions.py", "FinanceAssumptions", "working_capital_months"): (
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_out_of_range_finance_input_identifies_its_field"
        ),
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_published_finance_editor_endpoints_are_admitted"
        ),
        (
            "tests/test_mutation38_simulation_finance_default_cash_v2.py:"
            ":test_omitted_working_reserve_funds_and_recovers_a_full_oper"
            "ating_year"
        ),
    ),
    ("app/simulation/assumptions.py", "GrowthAssumptions", "adult_weight_age_months"): (
        (
            "tests/test_mutation38_simulation_growth_events.py::test_json"
            "_growth_calendar_preserves_inclusive_policy_endpoints"
        ),
    ),
    ("app/simulation/assumptions.py", "GrowthAssumptions", "sale_age_months"): (
        (
            "tests/test_mutation38_simulation_growth_events.py::test_json"
            "_growth_calendar_preserves_inclusive_policy_endpoints"
        ),
    ),
    ("app/simulation/assumptions.py", "GrowthAssumptions", "weight_by_age_months"): (
        (
            "tests/test_mutation38_simulation_growth_events.py::test_json"
            "_growth_curve_has_exactly_birth_through_month_twelve_and_per"
            "mits_plateaus"
        ),
    ),
    ("app/simulation/assumptions.py", "HerdAssumptions", "does"): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_json_integer_policy_retains_both_endpoints_and_rejects_o"
            "utside"
        ),
    ),
    (
        "app/simulation/assumptions.py",
        "HerdAssumptions",
        "foundation_doe_age_max_months",
    ): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_json_integer_policy_retains_both_endpoints_and_rejects_o"
            "utside"
        ),
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_omitted_json_policy_preserves_the_documented_reference_u"
            "nit"
        ),
    ),
    (
        "app/simulation/assumptions.py",
        "HerdAssumptions",
        "foundation_doe_age_min_months",
    ): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_json_integer_policy_retains_both_endpoints_and_rejects_o"
            "utside"
        ),
        (
            "tests/test_mutation38_simulation_foundation_validation_v2.py"
            "::test_public_impossible_foundation_minimum_has_an_actionabl"
            "e_field_error"
        ),
    ),
    ("app/simulation/assumptions.py", "HerdAssumptions", "max_breeding_does"): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_json_integer_policy_retains_both_endpoints_and_rejects_o"
            "utside"
        ),
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_omitted_json_policy_preserves_the_documented_reference_u"
            "nit"
        ),
    ),
    (
        "app/simulation/assumptions.py",
        "HerdAssumptions",
        "purchased_doe_settling_months",
    ): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_json_integer_policy_retains_both_endpoints_and_rejects_o"
            "utside"
        ),
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_omitted_json_policy_preserves_the_documented_reference_u"
            "nit"
        ),
    ),
    ("app/simulation/assumptions.py", "HerdEventAssumptions", "age_months"): (
        (
            "tests/test_mutation38_simulation_growth_events.py::test_nati"
            "ve_json_event_preserves_one_based_months_and_optional_age_bo"
            "unds"
        ),
    ),
    ("app/simulation/assumptions.py", "HerdEventAssumptions", "month"): (
        (
            "tests/test_mutation38_simulation_growth_events.py::test_nati"
            "ve_json_event_preserves_one_based_months_and_optional_age_bo"
            "unds"
        ),
    ),
    ("app/simulation/assumptions.py", "OptimizationAssumptions", "doe_scale_steps"): (
        (
            "tests/test_mutation38_simulation_optimizer_admission_v2.py::"
            "test_omitted_optimizer_axis_explores_its_documented_neighbor"
            "s"
        ),
        (
            "tests/test_mutation38_simulation_optimizer_admission_v2.py::"
            "test_optimizer_out_of_range_input_identifies_its_field"
        ),
        (
            "tests/test_mutation38_simulation_optimizer_admission_v2.py::"
            "test_saved_optimizer_endpoint_is_admitted"
        ),
        (
            "tests/test_mutation38_simulation_optimizer_middle_herd_finan"
            "cing.py::test_omitted_herd_size_axis_keeps_middle_size_for_e"
            "ach_financing_decision"
        ),
    ),
    (
        "app/simulation/assumptions.py",
        "OptimizationAssumptions",
        "festival_hold_radius_months",
    ): (
        (
            "tests/test_mutation38_simulation_optimizer_admission_v2.py::"
            "test_omitted_optimizer_axis_explores_its_documented_neighbor"
            "s"
        ),
        (
            "tests/test_mutation38_simulation_optimizer_admission_v2.py::"
            "test_optimizer_out_of_range_input_identifies_its_field"
        ),
        (
            "tests/test_mutation38_simulation_optimizer_admission_v2.py::"
            "test_saved_optimizer_endpoint_is_admitted"
        ),
    ),
    ("app/simulation/assumptions.py", "OptimizationAssumptions", "max_candidates"): (
        (
            "tests/test_mutation38_simulation_optimizer_admission_v2.py::"
            "test_default_optimizer_work_reservation_matches_the_publishe"
            "d_starting_budget"
        ),
        (
            "tests/test_mutation38_simulation_optimizer_admission_v2.py::"
            "test_optimizer_out_of_range_input_identifies_its_field"
        ),
        (
            "tests/test_mutation38_simulation_optimizer_admission_v2.py::"
            "test_saved_optimizer_endpoint_is_admitted"
        ),
    ),
    (
        "app/simulation/assumptions.py",
        "OptimizationAssumptions",
        "sale_age_radius_months",
    ): (
        (
            "tests/test_mutation38_simulation_optimizer_admission_v2.py::"
            "test_omitted_optimizer_axis_explores_its_documented_neighbor"
            "s"
        ),
        (
            "tests/test_mutation38_simulation_optimizer_admission_v2.py::"
            "test_optimizer_out_of_range_input_identifies_its_field"
        ),
        (
            "tests/test_mutation38_simulation_optimizer_admission_v2.py::"
            "test_saved_optimizer_endpoint_is_admitted"
        ),
    ),
    (
        "app/simulation/assumptions.py",
        "OptimizationAssumptions",
        "service_cull_radius_months",
    ): (
        (
            "tests/test_mutation38_simulation_optimizer_admission_v2.py::"
            "test_omitted_optimizer_axis_explores_its_documented_neighbor"
            "s"
        ),
        (
            "tests/test_mutation38_simulation_optimizer_admission_v2.py::"
            "test_optimizer_out_of_range_input_identifies_its_field"
        ),
        (
            "tests/test_mutation38_simulation_optimizer_admission_v2.py::"
            "test_saved_optimizer_endpoint_is_admitted"
        ),
    ),
    ("app/simulation/assumptions.py", "ParityMultipliers", "conception_rate"): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_json_parity_tables_support_one_through_twelve_and_the_fu"
            "ll_multiplier_range"
        ),
    ),
    ("app/simulation/assumptions.py", "ParityMultipliers", "litter_size"): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_json_parity_tables_support_one_through_twelve_and_the_fu"
            "ll_multiplier_range"
        ),
    ),
    (
        "app/simulation/assumptions.py",
        "ReproductionAssumptions",
        "age_at_first_breeding_months",
    ): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_json_integer_policy_retains_both_endpoints_and_rejects_o"
            "utside"
        ),
    ),
    ("app/simulation/assumptions.py", "ReproductionAssumptions", "gestation_months"): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_json_integer_policy_retains_both_endpoints_and_rejects_o"
            "utside"
        ),
    ),
    ("app/simulation/assumptions.py", "ReproductionAssumptions", "lactation_months"): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_json_integer_policy_retains_both_endpoints_and_rejects_o"
            "utside"
        ),
    ),
    (
        "app/simulation/assumptions.py",
        "ReproductionAssumptions",
        "max_services_before_cull",
    ): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_json_integer_policy_retains_both_endpoints_and_rejects_o"
            "utside"
        ),
    ),
    (
        "app/simulation/assumptions.py",
        "ReproductionAssumptions",
        "months_open_before_breeding",
    ): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_json_integer_policy_retains_both_endpoints_and_rejects_o"
            "utside"
        ),
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_omitted_json_policy_preserves_the_documented_reference_u"
            "nit"
        ),
    ),
    (
        "app/simulation/assumptions.py",
        "RiskAssumptions",
        "disease_outbreak_duration_months",
    ): (
        (
            "tests/test_mutation38_monte_carlo_native_contracts_v5.py::te"
            "st_integral_risk_admission_matches_published_editor_limits"
        ),
        (
            "tests/test_mutation38_monte_carlo_native_contracts_v5.py::te"
            "st_omitted_event_durations_retain_the_existing_twelve_month_"
            "episode_calendar"
        ),
    ),
    ("app/simulation/assumptions.py", "RiskAssumptions", "drought_duration_months"): (
        (
            "tests/test_mutation38_monte_carlo_native_contracts_v5.py::te"
            "st_integral_risk_admission_matches_published_editor_limits"
        ),
        (
            "tests/test_mutation38_uncertainty_geometry_narrative_v5.py::"
            "test_omitted_episode_durations_preserve_the_full_two_year_ev"
            "ent_calendar"
        ),
    ),
    (
        "app/simulation/assumptions.py",
        "RiskAssumptions",
        "market_crash_duration_months",
    ): (
        (
            "tests/test_mutation38_monte_carlo_native_contracts_v5.py::te"
            "st_integral_risk_admission_matches_published_editor_limits"
        ),
        (
            "tests/test_mutation38_monte_carlo_native_contracts_v5.py::te"
            "st_omitted_event_durations_retain_the_existing_twelve_month_"
            "episode_calendar"
        ),
    ),
    ("app/simulation/assumptions.py", "RiskAssumptions", "monte_carlo_runs"): (
        (
            "tests/test_mutation38_monte_carlo_native_contracts_v5.py::te"
            "st_integral_risk_admission_matches_published_editor_limits"
        ),
        (
            "tests/test_mutation38_monte_carlo_native_contracts_v5.py::te"
            "st_omitted_run_controls_match_the_existing_editor_run_fixtur"
            "e"
        ),
    ),
    ("app/simulation/assumptions.py", "RiskAssumptions", "seed"): (
        (
            "tests/test_mutation38_monte_carlo_native_contracts_v5.py::te"
            "st_integral_risk_admission_matches_published_editor_limits"
        ),
        (
            "tests/test_mutation38_monte_carlo_native_contracts_v5.py::te"
            "st_omitted_run_controls_match_the_existing_editor_run_fixtur"
            "e"
        ),
    ),
    (
        "app/simulation/assumptions.py",
        "RiskAssumptions",
        "within_run_price_variation",
    ): (
        (
            "tests/test_mutation38_monte_carlo_native_contracts_v5.py::te"
            "st_omitted_annual_price_variation_preserves_the_enabled_publ"
            "ic_forecast"
        ),
    ),
    ("app/simulation/assumptions.py", "SalesAssumptions", "eid_month"): (
        (
            "tests/test_mutation38_simulation_market_calendar_admission.p"
            "y::test_calendar_and_hold_controls_admit_both_published_boun"
            "daries"
        ),
        (
            "tests/test_mutation38_simulation_market_calendar_admission.p"
            "y::test_calendar_and_hold_controls_reject_values_outside_edi"
            "tor_range"
        ),
        (
            "tests/test_mutation38_simulation_market_calendar_admission.p"
            "y::test_omitted_legacy_month_does_not_add_a_recurring_januar"
            "y_premium"
        ),
    ),
    ("app/simulation/assumptions.py", "SalesAssumptions", "festival_date_overrides"): (
        (
            "tests/test_mutation38_simulation_market_calendar_admission.p"
            "y::test_authored_local_festival_dates_preserve_the_document_"
            "budget"
        ),
    ),
    ("app/simulation/assumptions.py", "SalesAssumptions", "festival_date_source"): (
        (
            "tests/test_mutation38_simulation_market_calendar_admission.p"
            "y::test_authored_date_provenance_preserves_the_full_editor_d"
            "ocument"
        ),
    ),
    ("app/simulation/assumptions.py", "SalesAssumptions", "festival_hold_months"): (
        (
            "tests/test_mutation38_simulation_market_calendar_admission.p"
            "y::test_calendar_and_hold_controls_admit_both_published_boun"
            "daries"
        ),
        (
            "tests/test_mutation38_simulation_market_calendar_admission.p"
            "y::test_calendar_and_hold_controls_reject_values_outside_edi"
            "tor_range"
        ),
        (
            "tests/test_mutation38_simulation_market_hold_calendar_v2.py:"
            ":test_omitted_hold_setting_keeps_the_reference_two_month_fin"
            "ishing_policy"
        ),
    ),
    ("app/simulation/assumptions.py", "SalesAssumptions", "festival_sale_months"): (
        (
            "tests/test_mutation38_simulation_market_calendar_admission.p"
            "y::test_forty_authored_festival_months_survive_without_calen"
            "dar_replacement"
        ),
        (
            "tests/test_mutation38_simulation_market_calendar_admission.p"
            "y::test_forty_one_festival_months_are_rejected_at_the_author"
            "ed_document_boundary"
        ),
    ),
    (
        "app/simulation/assumptions.py",
        "SalesAssumptions",
        "monthly_meat_price_multipliers",
    ): (
        (
            "tests/test_mutation38_simulation_market_calendar_admission.p"
            "y::test_authored_price_curve_requires_a_complete_twelve_mont"
            "h_calendar"
        ),
    ),
    ("app/simulation/assumptions.py", "SubsidyReceipt", "month"): (
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_receipt_month_editor_endpoints_remain_admitted"
        ),
        (
            "tests/test_mutation38_simulation_finance_admission_v2.py::te"
            "st_receipt_month_outside_the_editor_range_has_a_field_error"
        ),
    ),
    ("app/simulation/backward_planner.py", "PlannerTarget", "model_config"): (
        (
            "tests/test_mutation38_backward_plan_native_story_v2.py::test"
            "_native_calendar_target_rejects_a_coerced_head_count"
        ),
    ),
    ("app/simulation/daily_ops.py", "AnimalStartSpec", "age_months"): (
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_starting_animal_age_endpoints_preserve_the_reported_a"
            "ge"
        ),
    ),
    ("app/simulation/daily_ops.py", "AnimalStartSpec", "bred_days_ago"): (
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_real_species_service_phase_endpoints_are_admitted"
        ),
    ),
    ("app/simulation/daily_ops.py", "AnimalStartSpec", "days_in_bucket"): (
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_bucket_residence_outside_the_supported_history_is_rej"
            "ected"
        ),
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_bucket_residence_supported_endpoints_are_preserved"
        ),
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_omitted_animal_history_is_new_residence_and_not_a_dep"
            "endent_kid"
        ),
    ),
    ("app/simulation/daily_ops.py", "AnimalStartSpec", "dependent_kid"): (
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_animal_tag_supported_endpoints_are_preserved"
        ),
    ),
    ("app/simulation/daily_ops.py", "AnimalStartSpec", "model_config"): (
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_animal_and_policy_integers_reject_json_coercion"
        ),
    ),
    ("app/simulation/daily_ops.py", "AnimalStartSpec", "tag"): (
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_animal_tag_supported_endpoints_are_preserved"
        ),
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_blank_or_oversized_animal_tag_is_rejected"
        ),
    ),
    ("app/simulation/daily_ops.py", "DailyOpsInput", "animals"): (
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_coherent_animal_sex_and_bucket_state_is_admitted"
        ),
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_empty_or_oversized_starting_herd_is_rejected"
        ),
    ),
    ("app/simulation/daily_ops.py", "DailyOpsInput", "horizon_days"): (
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_omitted_daily_window_and_seed_match_the_operator_form"
        ),
    ),
    ("app/simulation/daily_ops.py", "DailyOpsInput", "model_config"): (
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_animal_and_policy_integers_reject_json_coercion"
        ),
    ),
    ("app/simulation/daily_ops.py", "DailyOpsInput", "seed"): (
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_daily_seed_rejects_values_outside_its_shared_integer_"
            "contract"
        ),
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_omitted_daily_window_and_seed_match_the_operator_form"
        ),
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_seed_integer_endpoints_are_echoable_without_coercion"
        ),
    ),
    ("app/simulation/daily_ops.py", "DailyOpsParams", "buck_doe_ratio"): (
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_operational_policy_integer_endpoints_are_preserved"
        ),
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_operational_policy_outside_the_supported_range_is_rej"
            "ected"
        ),
    ),
    ("app/simulation/daily_ops.py", "DailyOpsParams", "failed_services_before_cull"): (
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_operational_policy_integer_endpoints_are_preserved"
        ),
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_operational_policy_outside_the_supported_range_is_rej"
            "ected"
        ),
    ),
    ("app/simulation/daily_ops.py", "DailyOpsParams", "male_sale_age_months"): (
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_omitted_operational_policy_matches_live_species_and_m"
            "eat_sale_rules"
        ),
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_operational_policy_integer_endpoints_are_preserved"
        ),
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_operational_policy_outside_the_supported_range_is_rej"
            "ected"
        ),
    ),
    ("app/simulation/daily_ops.py", "DailyOpsParams", "max_doe_age_months"): (
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_operational_policy_integer_endpoints_are_preserved"
        ),
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_operational_policy_outside_the_supported_range_is_rej"
            "ected"
        ),
    ),
    ("app/simulation/daily_ops.py", "DailyOpsParams", "model_config"): (
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_animal_and_policy_integers_reject_json_coercion"
        ),
    ),
    ("app/simulation/daily_ops.py", "_Animal", "born_in_sim"): (
        (
            "tests/test_000_mutation38_simulation_progress.py::test_colli"
            "ding_newborn_tags_complete_without_losing_existing_animals"
        ),
    ),
    ("app/simulation/daily_ops.py", "_Animal", "failed_services"): (
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_two_failed_services_follow_the_real_next_heat_and_c"
            "ull_policy"
        ),
    ),
    ("app/simulation/daily_ops.py", "_Animal", "kidding_watched"): (
        (
            "tests/test_mutation38_simulation_daily_operator_explanations"
            "_v6.py::test_actual_due_day_has_the_kidding_watch_even_when_"
            "the_doe_started_mid_pregnancy"
        ),
    ),
    ("app/simulation/daily_ops.py", "_Animal", "litter_ordinal"): (
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_pregnancy_milestones_and_later_service_match"
            "_species_calendar"
        ),
    ),
    ("app/simulation/daily_ops.py", "_Animal", "moved_to_delivery"): (
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_pregnancy_milestones_and_later_service_match"
            "_species_calendar"
        ),
    ),
    ("app/simulation/daily_ops.py", "_Animal", "moved_to_late"): (
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_pregnancy_milestones_and_later_service_match"
            "_species_calendar"
        ),
    ),
    ("app/simulation/daily_ops.py", "_Animal", "vaccine_booster_done"): (
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_pregnancy_milestones_and_later_service_match"
            "_species_calendar"
        ),
    ),
    ("app/simulation/daily_ops.py", "_Animal", "vaccine_primary_done"): (
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_pregnancy_milestones_and_later_service_match"
            "_species_calendar"
        ),
    ),
    ("app/simulation/market.py", "FestivalOccurrence", "uncertainty_days"): (
        (
            "tests/test_mutation38_simulation_market_release_calendar_v2."
            "py::test_public_release_advisory_dates_and_strictly_after_bo"
            "undaries_remain_complete"
        ),
    ),
    ("app/simulation/planner.py", "SaleTarget", "model_config"): (
        (
            "tests/test_mutation38_simulation_forward_cohort_planning_v4."
            "py::test_native_sale_target_requires_real_typed_month_and_he"
            "ad_count"
        ),
    ),
    ("app/simulation/planner.py", "SaleTarget", "month"): (
        (
            "tests/test_mutation38_planner_native_risk_completion_v4.py::"
            "test_native_first_month_target_is_admitted_and_month_zero_is"
            "_rejected"
        ),
    ),
    ("app/simulation/results.py", "MonteCarloResult", "npv_histogram_counts"): (
        (
            "tests/test_mutation38_monte_carlo_native_contracts_v5.py::te"
            "st_real_debt_free_loss_forecast_reports_measurable_risk_and_"
            "optional_sampling"
        ),
        (
            "tests/test_mutation38_uncertainty_geometry_narrative_v5.py::"
            "test_public_uncertainty_document_rejects_a_missing_or_extra_"
            "histogram_component"
        ),
    ),
    ("app/simulation/results.py", "MonteCarloResult", "npv_histogram_edges"): (
        (
            "tests/test_mutation38_monte_carlo_native_contracts_v5.py::te"
            "st_real_debt_free_loss_forecast_reports_measurable_risk_and_"
            "optional_sampling"
        ),
        (
            "tests/test_mutation38_uncertainty_geometry_narrative_v5.py::"
            "test_public_uncertainty_document_rejects_a_missing_or_extra_"
            "histogram_component"
        ),
    ),
}

ASSIGNMENT_ORACLES = {
    ("app/api/breeding.py", "BREEDING_CANDIDATE_DEFAULT_LIMIT"): (
        ("tests/test_contract_drift.py::test_committed_openapi_json_matches_the_live_schema"),
    ),
    ("app/api/breeding.py", "BREEDING_CANDIDATE_MAX_LIMIT"): (
        ("tests/test_contract_drift.py::test_committed_openapi_json_matches_the_live_schema"),
    ),
    ("app/api/breeding.py", "BREEDING_HISTORY_DEFAULT_LIMIT"): (
        ("tests/test_contract_drift.py::test_committed_openapi_json_matches_the_live_schema"),
    ),
    ("app/api/breeding.py", "BREEDING_HISTORY_MAX_LIMIT"): (
        ("tests/test_contract_drift.py::test_committed_openapi_json_matches_the_live_schema"),
    ),
    ("app/api/buckets.py", "BUCKET_ANIMAL_PREVIEW_LIMIT"): (
        ("tests/test_animals_extended.py::test_buckets_board_returns_all_ten_in_spec_order"),
        (
            "tests/test_query_performance.py::test_bucket_board_preview_a"
            "nd_history_queries_stay_bounded"
        ),
    ),
    ("app/api/dashboard.py", "INSURANCE_EXPIRING_WINDOW_DAYS"): (
        (
            "tests/test_mutation38_dashboard_native_contract_v4.py::test_"
            "native_pending_ultrasound_and_policy_horizons_keep_exact_edg"
            "es"
        ),
    ),
    ("app/api/dashboard.py", "KIDDING_DUE_WINDOW_DAYS"): (
        (
            "tests/test_mutation38_dashboard_native_contract_v4.py::test_"
            "native_kidding_preview_balances_backlog_with_today_and_fourt"
            "een_day_edge"
        ),
    ),
    ("app/api/dashboard.py", "RECENT_WEIGHTS_LIMIT"): (
        (
            "tests/test_mutation38_dashboard_native_contract_v4.py::test_"
            "recent_native_weights_keep_ten_observations_and_their_own_id"
            "entities"
        ),
    ),
    ("app/api/health.py", "HEALTH_LOOKUP_DEFAULT_LIMIT"): (
        (
            "tests/test_mutation38_health_round_progress_contracts.py::te"
            "st_health_lookup_reports_fifty_by_default_and_accepts_exactl"
            "y_one_hundred"
        ),
    ),
    ("app/api/health.py", "HEALTH_LOOKUP_MAX_LIMIT"): (
        (
            "tests/test_mutation38_health_round_progress_contracts.py::te"
            "st_health_lookup_reports_fifty_by_default_and_accepts_exactl"
            "y_one_hundred"
        ),
    ),
    ("app/api/kidding.py", "DUE_DEFAULT_LIMIT"): (
        (
            "tests/test_mutation38_livestock_kidding_wire.py::test_kiddin"
            "g_pages_default_to_thirty_rows_and_zero_offsets"
        ),
    ),
    ("app/api/kidding.py", "DUE_MAX_LIMIT"): (
        (
            "tests/test_mutation38_livestock_kidding_wire.py::test_kiddin"
            "g_due_pages_accept_one_hundred_and_reject_one_hundred_one"
        ),
    ),
    ("app/api/ops_simulation.py", "_BIRTH_AMPLIFICATION_FACTOR"): (
        (
            "tests/test_mutation38_simulation_daily_api_admission_account"
            "ing_v3.py::test_completed_daily_run_books_its_documented_amp"
            "lified_work"
        ),
    ),
    ("app/api/planner.py", "PLAN_QUOTA_LOCK_NAMESPACE"): (
        (
            "tests/test_mutation38_saved_decision_native_concurrency_v3.p"
            "y::test_saved_decision_create_is_independent_of_other_native"
            "_workflow_quota_locks"
        ),
    ),
    ("app/api/screening.py", "MAX_IN_FLIGHT_SCREENING_IMAGES_PER_FARM"): (
        (
            "tests/test_mutation38_screening_intake_capacity.py::test_las"
            "t_available_registration_consumes_the_literal_photo_capacity"
            "[farm]"
        ),
    ),
    ("app/api/screening.py", "MAX_OPEN_SCREENING_BATCHES_PER_FARM"): (
        (
            "tests/test_mutation38_screening_intake_capacity.py::test_far"
            "m_admits_exactly_five_live_walkthroughs"
        ),
    ),
    ("app/api/screening.py", "MAX_OPEN_SCREENING_BATCH_AGE"): (
        (
            "tests/test_mutation38_screening_intake_capacity.py::test_wal"
            "kthrough_upload_window_includes_the_exact_25_hour_boundary[e"
            "xpired]"
        ),
        (
            "tests/test_mutation38_screening_intake_capacity.py::test_wal"
            "kthrough_upload_window_includes_the_exact_25_hour_boundary[i"
            "nside-grace]"
        ),
    ),
    ("app/api/screening.py", "MAX_SCREENING_IMAGES_PER_BATCH"): (
        (
            "tests/test_mutation38_screening_intake_capacity.py::test_las"
            "t_available_registration_consumes_the_literal_photo_capacity"
            "[walkthrough]"
        ),
    ),
    ("app/api/screening.py", "SCREENING_BATCH_LIST_MAX_LIMIT"): (
        (
            "tests/test_mutation38_screening_intake_capacity.py::test_pub"
            "lished_page_contract_keeps_independent_default_and_ceiling[w"
            "alkthrough-page]"
        ),
    ),
    ("app/api/screening.py", "SCREENING_EXPORT_MAX_RECORDS"): (
        (
            "tests/test_mutation38_screening_intake_capacity.py::test_pub"
            "lished_page_contract_keeps_independent_default_and_ceiling[t"
            "raining-export]"
        ),
    ),
    ("app/api/screening.py", "SCREENING_INTAKE_LOCK_NAMESPACE"): (
        (
            "tests/test_mutation38_screening_read_contracts.py::test_inta"
            "ke_uses_the_established_cross_runtime_farm_mutex"
        ),
    ),
    ("app/api/screening.py", "SCREENING_LIST_DEFAULT_LIMIT"): (
        (
            "tests/test_mutation38_screening_intake_capacity.py::test_pub"
            "lished_page_contract_keeps_independent_default_and_ceiling[r"
            "eview-queue]"
        ),
    ),
    ("app/api/screening.py", "SCREENING_LIST_MAX_LIMIT"): (
        (
            "tests/test_mutation38_screening_intake_capacity.py::test_pub"
            "lished_page_contract_keeps_independent_default_and_ceiling[r"
            "eview-queue]"
        ),
    ),
    ("app/api/screening.py", "SCREENING_STATS_MAX_DAYS"): (
        (
            "tests/test_mutation38_screening_read_contracts.py::test_prov"
            "ider_scoreboard_retains_the_one_year_window"
        ),
    ),
    ("app/api/simulation.py", "MAX_COMPARE_IDS"): (
        (
            "tests/test_mutation38_saved_scenario_native_execution_v1.py:"
            ":test_compare_admits_five_distinct_scenarios_and_rejects_six"
        ),
    ),
    ("app/api/simulation.py", "SCENARIO_QUOTA_LOCK_NAMESPACE"): (
        (
            "tests/test_mutation38_saved_decision_native_concurrency_v3.p"
            "y::test_saved_decision_create_is_independent_of_other_native"
            "_workflow_quota_locks"
        ),
    ),
    ("app/models/constants.py", "BUCK_ROTATION_DAYS"): (
        "tests/test_unit_extended.py::test_breed_constants_match_spec",
    ),
    ("app/models/constants.py", "MAX_AGE_MONTHS"): (
        (
            "tests/test_mutation38_policy_input_caps.py::test_documented_"
            "maximum_purchase_inputs_remain_valid"
        ),
        "tests/test_unit_extended.py::test_purchase_batch_invalid",
    ),
    ("app/models/constants.py", "MAX_BATCH_COUNT"): (
        (
            "tests/test_mutation38_policy_input_caps.py::test_documented_"
            "maximum_purchase_inputs_remain_valid"
        ),
        "tests/test_unit_extended.py::test_purchase_batch_invalid",
    ),
    ("app/models/constants.py", "MAX_RECUR_DAYS"): (
        (
            "tests/test_mutation38_policy_input_caps.py::test_documented_"
            "maximum_manual_task_inputs_remain_valid"
        ),
        "tests/test_unit_extended.py::test_task_create_invalid",
    ),
    ("app/models/constants.py", "MAX_TASK_TITLE_LENGTH"): (
        (
            "tests/test_mutation38_policy_input_caps.py::test_documented_"
            "maximum_manual_task_inputs_remain_valid"
        ),
    ),
    ("app/models/constants.py", "MAX_WITHDRAWAL_DAYS"): (
        ("tests/test_health_extended.py::test_omitted_event_date_enforces_withdrawal_ceiling"),
    ),
    ("app/models/constants.py", "MEAT_SALE_AGE_MONTHS"): (
        "tests/test_unit_extended.py::test_breed_constants_match_spec",
    ),
    ("app/models/helpers.py", "_QUARANTINE_PROTOCOL"): (
        (
            "tests/test_mutation38_livestock_quarantine_calendar.py::test"
            "_purchase_quarantine_calendar_has_all_literal_due_dates_and_"
            "day_labels"
        ),
    ),
    ("app/models/species.py", "GOAT_PROFILE"): (
        (
            "tests/test_mutation38_livestock_young_policy.py::test_goat_y"
            "oung_remain_with_the_dam_until_weaning"
        ),
        "tests/test_schema_parity.py::test_species_policy_profiles_are_coherent",
        "tests/test_unit_extended.py::test_breed_constants_match_spec",
    ),
    ("app/schemas/feeding.py", "RecipeCodeStr"): (
        (
            "tests/test_mutation38_feeding_schema_contracts.py::test_reci"
            "pe_code_accepts_its_native_wire_boundaries[longest-code]"
        ),
        (
            "tests/test_mutation38_feeding_schema_contracts.py::test_reci"
            "pe_code_accepts_its_native_wire_boundaries[shortest-code]"
        ),
        (
            "tests/test_mutation38_feeding_schema_contracts.py::test_reci"
            "pe_code_rejects_invalid_native_envelopes[overlong-code]"
        ),
    ),
    ("app/schemas/finance.py", "_MAX_POLICY_SPAN_DAYS"): (
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_i"
            "nsurance_native_input_preserves_the_independent_five_year_ho"
            "rizon[beyond-cap]"
        ),
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_i"
            "nsurance_native_input_preserves_the_independent_five_year_ho"
            "rizon[five-leap-year-cap]"
        ),
        (
            "tests/test_mutation38_finance_insurance_contracts.py::test_p"
            "olicy_input_preserves_the_declared_text_boundaries[number-si"
            "ngle]"
        ),
    ),
    ("app/schemas/health.py", "MAX_NEXT_DUE_DAYS"): (
        (
            "tests/test_mutation38_health_schema_contracts.py::test_healt"
            "h_dates_accept_valid_equalities_and_inclusive_ceiling[maximu"
            "m-next-dose-period]"
        ),
        (
            "tests/test_mutation38_health_schema_contracts.py::test_next_"
            "dose_requires_a_later_date_within_the_ten_year_ceiling[beyon"
            "d-ten-years]"
        ),
    ),
    ("app/schemas/ops_simulation.py", "MAX_LEDGER_HEAD_DAYS"): (
        "tests/test_daily_ops_api.py::test_run_rejects_oversized_result_payloads",
    ),
    ("app/schemas/screening.py", "MAX_SCREENING_UPLOAD_BYTES"): (
        (
            "tests/test_mutation38_screening_batch_contracts.py::test_upl"
            "oad_registration_metadata_keeps_the_literal_one_byte_to_25_m"
            "ib_band[25-mib]"
        ),
        (
            "tests/test_mutation38_screening_batch_contracts.py::test_upl"
            "oad_registration_metadata_keeps_the_literal_one_byte_to_25_m"
            "ib_band[over-25-mib]"
        ),
    ),
    ("app/services/breeding.py", "MAX_HEAT_CYCLE_NUMBER"): (
        (
            "tests/test_mutation38_livestock_service_calendar_caps.py::te"
            "st_native_recorded_failed_history_saturates_at_schema_cycle9"
            "9"
        ),
    ),
    ("app/services/breeding.py", "PREGNANCY_LATE_MOVE_DAYS_BEFORE_EKD"): (
        (
            "tests/test_mutation38_livestock_service_calendar_caps.py::te"
            "st_confirmed_pregnancy_steps_up_at_literal_gestation_day100"
        ),
    ),
    ("app/services/cadence.py", "_BUCK_ROTATION_LOOKBACK_DAYS"): (
        (
            "tests/test_mutation38_workflow_buck_rotation_window_formatte"
            "d.py::test_buck_rotation_history_includes_the_365th_day_in_e"
            "very_status"
        ),
    ),
    ("app/services/cadence.py", "_CALENDAR_ROUNDS"): (
        (
            "tests/test_mutation38_cadence_calendar_native_contracts.py::"
            "test_calendar_dedupe_keeps_later_series_and_snapshots_the_ac"
            "tual_herd"
        ),
    ),
    ("app/services/cadence.py", "_INTERVAL_FORWARD_DEDUPE_DAYS"): (
        (
            "tests/test_mutation38_workflow_cadence_forward_window.py::te"
            "st_interval_forward_window_ends_on_the_thirtieth_day"
        ),
    ),
    ("app/services/cadence.py", "_INTERVAL_ROUNDS"): (
        (
            "tests/test_mutation38_cadence_interval_expiry.py::test_inter"
            "val_round_expires_only_after_its_last_inclusive_day"
        ),
    ),
    ("app/services/cadence.py", "_MAX_BUCK_SCAN"): (
        (
            "tests/test_mutation38_cadence_native_scan_conservation.py::t"
            "est_buck_rotation_scan_conserves_its_documented_first_five_h"
            "undred_actual_bucks"
        ),
    ),
    ("app/services/cadence.py", "_MAX_REORDER_INGREDIENTS"): (
        (
            "tests/test_mutation38_cadence_native_scan_conservation.py::t"
            "est_feed_reorder_scan_conserves_the_documented_first_hundred"
            "_ingredient_duties"
        ),
    ),
    ("app/services/dashboard.py", "BAKRID_HOLD_WINDOW_MONTHS"): (
        (
            "tests/test_mutation38_dashboard_native_contract_v4.py::test_"
            "native_month_end_births_do_not_break_the_actual_calendar_adv"
            "isory"
        ),
    ),
    ("app/services/finance.py", "INSURANCE_RENEWAL_LEAD_DAYS"): (
        (
            "tests/test_mutation38_livestock_finance_native_boundaries.py"
            "::test_native_registration_literal_five_year_backstop_before"
            "_any_register_effect"
        ),
    ),
    ("app/services/finance.py", "MAX_RENEWAL_SPAN_DAYS"): (
        (
            "tests/test_mutation38_livestock_finance_native_boundaries.py"
            "::test_native_covered_animal_gate_accepts_active_and_rejects"
            "_real_sold_and_foreign_cover"
        ),
        (
            "tests/test_mutation38_livestock_finance_native_boundaries.py"
            "::test_native_registration_literal_five_year_backstop_before"
            "_any_register_effect"
        ),
    ),
    ("app/services/health.py", "LEGACY_SCHEDULE_SCAN_LIMIT"): (
        (
            "tests/test_mutation38_health_schedule_boundaries.py::test_le"
            "gacy_schedule_respects_the_literal_five_hundred_fact_window"
        ),
    ),
    ("app/services/screening/budget.py", "BUDGET_LOCK_NAMESPACE"): (
        (
            "tests/test_mutation38_screening_budget_native_contracts.py::"
            "test_paid_admission_keeps_the_established_cross_runtime_farm"
            "_lease"
        ),
    ),
    ("app/services/screening/detect.py", "MAX_DETECTED_GOATS"): (
        (
            "tests/test_mutation38_screening_detection_geometry.py::test_"
            "one_detector_reply_keeps_at_most_twenty_usable_goats[halluci"
            "nated-extra]"
        ),
        (
            "tests/test_mutation38_screening_detection_geometry.py::test_"
            "one_detector_reply_keeps_at_most_twenty_usable_goats[twentie"
            "th]"
        ),
    ),
    ("app/services/screening/gate.py", "MAX_OBSERVATIONS"): (
        (
            "tests/test_mutation38_screening_gate_contracts.py::test_one_"
            "gate_reply_contains_at_most_eight_visible_observations[eight"
            "-visible-signs]"
        ),
        (
            "tests/test_mutation38_screening_gate_contracts.py::test_one_"
            "gate_reply_contains_at_most_eight_visible_observations[overf"
            "ull]"
        ),
    ),
    ("app/services/screening/images.py", "JPEG_QUALITY"): (
        (
            "tests/test_mutation38_canonical_screening_jpeg_contracts.py:"
            ":test_camera_normalization_has_the_known_canonical_jpeg_quan"
            "tization"
        ),
    ),
    ("app/services/screening/pipeline.py", "PENDING_OBJECT_RETRY_AFTER"): (
        (
            "tests/test_mutation38_screening_queue_native_contracts.py::t"
            "est_a_missing_live_upload_refunds_the_claim_and_reprobes_in_"
            "five_minutes"
        ),
    ),
    ("app/services/screening/pipeline.py", "_CLAIM_CANDIDATE_WINDOW"): (
        (
            "tests/test_mutation38_screening_queue_native_contracts.py::t"
            "est_native_deep_backlog_keeps_the_two_thousand_candidate_fen"
            "ce"
        ),
    ),
    ("app/services/screening/s3.py", "_PRESIGN_EXPIRY_CAP_SECONDS"): (
        (
            "tests/test_mutation38_screening_native_presign_day_cap.py::t"
            "est_native_runtime_overlong_expiry_obeys_the_signed_day_cap["
            "get]"
        ),
        (
            "tests/test_mutation38_screening_storage_native_contracts.py:"
            ":test_presigned_wire_policy_keeps_one_byte_admission_and_the"
            "_day_expiry_cap"
        ),
    ),
    ("app/services/screening/s3.py", "_S3_CONNECT_TIMEOUT_SECONDS"): (
        (
            "tests/test_mutation38_screening_storage_native_contracts.py:"
            ":test_real_clients_apply_the_finite_worker_socket_and_retry_"
            "policy"
        ),
    ),
    ("app/services/screening/s3.py", "_S3_DELETE_CONNECT_TIMEOUT_SECONDS"): (
        (
            "tests/test_mutation38_screening_storage_native_contracts.py:"
            ":test_real_clients_apply_the_finite_worker_socket_and_retry_"
            "policy"
        ),
    ),
    ("app/services/screening/s3.py", "_S3_DELETE_READ_TIMEOUT_SECONDS"): (
        (
            "tests/test_mutation38_screening_storage_native_contracts.py:"
            ":test_real_clients_apply_the_finite_worker_socket_and_retry_"
            "policy"
        ),
    ),
    ("app/services/screening/s3.py", "_S3_DOWNLOAD_DEADLINE_SECONDS"): (
        (
            "tests/test_mutation38_screening_storage_native_contracts.py:"
            ":test_download_deadline_is_closed_at_twenty_seconds[at-deadl"
            "ine]"
        ),
        (
            "tests/test_mutation38_screening_storage_native_contracts.py:"
            ":test_download_deadline_is_closed_at_twenty_seconds[before-d"
            "eadline]"
        ),
    ),
    ("app/services/screening/s3.py", "_S3_PERMANENT_DELETE_TIMEOUT_BUDGET_SECONDS"): (
        (
            "tests/test_mutation38_screening_delete_timeout_diagnostic_co"
            "ntracts.py::test_advertised_purge_timeout_budget_covers_a_co"
            "mplete_native_sdk_path"
        ),
    ),
    ("app/services/screening/s3.py", "_S3_READ_TIMEOUT_SECONDS"): (
        (
            "tests/test_mutation38_screening_storage_native_contracts.py:"
            ":test_real_clients_apply_the_finite_worker_socket_and_retry_"
            "policy"
        ),
    ),
    ("app/services/screening/s3.py", "_S3_TOTAL_MAX_ATTEMPTS"): (
        (
            "tests/test_mutation38_screening_storage_native_contracts.py:"
            ":test_real_clients_apply_the_finite_worker_socket_and_retry_"
            "policy"
        ),
    ),
    ("app/services/screening/s3.py", "_STORAGE_CACHE_LIMIT"): (
        (
            "tests/test_mutation38_screening_storage_native_contracts.py:"
            ":test_real_settings_cache_preserves_its_thirty_two_instance_"
            "fifo_window"
        ),
    ),
    ("app/services/screening/specialists.py", "MAX_SPECIALIST_CONDITIONS"): (
        (
            "tests/test_mutation38_screening_specialist_parser_contracts."
            "py::test_specialist_answer_retains_the_first_five_valid_cond"
            "itions"
        ),
    ),
    ("app/services/simulation_calibration.py", "_SALE_WEIGHT_FALLBACK_MAX_DAYS"): (
        (
            "tests/test_mutation38_simulation_calibration_exit_weight_win"
            "dow_v3.py::test_actual_sales_accept_thirty_day_weight_and_ex"
            "clude_older_and_post_exit_weights"
        ),
    ),
    ("app/services/tasks.py", "MANUAL_TASK_QUEUE_LOCK_NAMESPACE"): (
        (
            "tests/test_mutation38_task_provisioning_queue_independence.p"
            "y::test_public_manual_duty_creation_does_not_join_the_team_p"
            "rovisioning_mutex"
        ),
    ),
    ("app/simulation/assumptions.py", "FiniteFloat"): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_json_parity_tables_support_one_through_twelve_and_the_fu"
            "ll_multiplier_range"
        ),
    ),
    ("app/simulation/assumptions.py", "MAX_FODDER_ACRES"): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_json_magnitude_ceiling_admits_the_endpoint_and_rejects_t"
            "he_next_unit"
        ),
    ),
    ("app/simulation/assumptions.py", "MAX_FODDER_YIELD_T"): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_json_magnitude_ceiling_admits_the_endpoint_and_rejects_t"
            "he_next_unit"
        ),
    ),
    ("app/simulation/assumptions.py", "MAX_LABOUR_PER_HEAD_THRESHOLD"): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_json_integer_policy_retains_both_endpoints_and_rejects_o"
            "utside"
        ),
    ),
    ("app/simulation/assumptions.py", "MAX_MONEY"): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_json_magnitude_ceiling_admits_the_endpoint_and_rejects_t"
            "he_next_unit"
        ),
    ),
    ("app/simulation/assumptions.py", "MAX_PLAN_EVENTS"): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_normal_json_plan_event_work_budget_is_exactly_five_hundr"
            "ed_documents"
        ),
    ),
    ("app/simulation/assumptions.py", "MAX_WEIGHT_KG"): (
        (
            "tests/test_mutation38_simulation_assumption_boundaries.py::t"
            "est_json_magnitude_ceiling_admits_the_endpoint_and_rejects_t"
            "he_next_unit"
        ),
    ),
    ("app/simulation/daily_ops.py", "MAX_HORIZON_DAYS"): (
        (
            "tests/test_mutation38_simulation_daily_input_envelope_v5.py:"
            ":test_run_and_engine_accept_the_supported_daily_window_endpo"
            "ints"
        ),
        (
            "tests/test_mutation38_simulation_daily_input_envelope_v5.py:"
            ":test_run_and_engine_reject_days_outside_the_supported_daily"
            "_window"
        ),
    ),
    ("app/simulation/daily_ops.py", "MAX_START_HEAD"): (
        (
            "tests/test_mutation38_simulation_daily_input_envelope_v5.py:"
            ":test_empty_or_oversized_starting_herd_is_rejected"
        ),
        (
            "tests/test_mutation38_simulation_daily_input_envelope_v5.py:"
            ":test_starting_herd_endpoint_keeps_every_unique_animal"
        ),
    ),
    ("app/simulation/daily_ops.py", "MIN_HORIZON_DAYS"): (
        (
            "tests/test_mutation38_simulation_daily_input_envelope_v5.py:"
            ":test_run_and_engine_accept_the_supported_daily_window_endpo"
            "ints"
        ),
        (
            "tests/test_mutation38_simulation_daily_input_envelope_v5.py:"
            ":test_run_and_engine_reject_days_outside_the_supported_daily"
            "_window"
        ),
    ),
    ("app/simulation/daily_ops.py", "_GROWER_AGE_DAYS"): (
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_annual_hazards_compound_to_the_configured_ad"
            "ult_and_grower_loss"
        ),
    ),
    ("app/simulation/daily_ops.py", "_HEAT_CYCLE_DAYS"): (
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_two_failed_services_follow_the_real_next_heat_and_c"
            "ull_policy"
        ),
    ),
    ("app/simulation/daily_ops.py", "_MAX_AGE_MONTHS_START"): (
        (
            "tests/test_mutation38_simulation_daily_input_envelope_v5.py:"
            ":test_starting_animal_age_endpoints_preserve_the_reported_ag"
            "e"
        ),
        (
            "tests/test_mutation38_simulation_daily_input_envelope_v5.py:"
            ":test_starting_animal_age_outside_the_shared_240_month_ceili"
            "ng_is_rejected"
        ),
    ),
    ("app/simulation/daily_ops.py", "_MAX_DOE_AGE_MONTHS"): (
        (
            "tests/test_mutation38_simulation_daily_state_admission_v2.py"
            "::test_omitted_operational_policy_matches_live_species_and_m"
            "eat_sale_rules"
        ),
    ),
    ("app/simulation/daily_ops.py", "_RESTING_FLUSH_DAYS"): (
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_pregnancy_milestones_and_later_service_match"
            "_species_calendar"
        ),
    ),
    ("app/simulation/daily_ops.py", "_WEANER_WINDOW_DAYS"): (
        (
            "tests/test_mutation38_simulation_daily_native_accounting_v3."
            "py::test_native_annual_hazards_compound_to_the_configured_ad"
            "ult_and_grower_loss"
        ),
    ),
    ("app/simulation/engine.py", "BREAK_EVEN_PASSES"): (
        (
            "tests/test_mutation38_simulation_price_search_work.py::test_"
            "price_search_work_allowance_matches_observed_full_native_eng"
            "ine_evaluations"
        ),
    ),
    ("app/simulation/market.py", "BAKRID_DATES_BY_YEAR"): (
        (
            "tests/test_mutation38_simulation_festival_projection_contrac"
            "ts.py::test_advertised_festival_years_have_actual_dated_advi"
            "ce"
        ),
        (
            "tests/test_mutation38_simulation_festival_projection_contrac"
            "ts.py::test_cited_global_projection_is_retained_in_dated_adv"
            "ice"
        ),
        (
            "tests/test_mutation38_simulation_festival_projection_contrac"
            "ts.py::test_festival_advice_follows_lunar_spacing_with_decla"
            "red_uncertainty"
        ),
        (
            "tests/test_mutation38_simulation_market_release_calendar_v2."
            "py::test_public_release_advisory_dates_and_strictly_after_bo"
            "undaries_remain_complete"
        ),
    ),
    ("app/simulation/montecarlo.py", "_BOOTSTRAP_RESAMPLES"): (
        (
            "tests/test_mutation38_monte_carlo_seeded_release_report_v1.p"
            "y::test_current_release_seeded_uncertainty_report_matches_na"
            "tive_reference"
        ),
    ),
    ("app/simulation/montecarlo.py", "_BOOTSTRAP_SEED_SALT"): (
        (
            "tests/test_mutation38_monte_carlo_seeded_release_report_v1.p"
            "y::test_current_release_seeded_uncertainty_report_matches_na"
            "tive_reference"
        ),
    ),
    ("app/simulation/planner.py", "CLOSE_GAPS_MAX_ITERATIONS"): (
        (
            "tests/test_mutation38_planner_native_risk_completion_v4.py::"
            "test_public_gap_closing_prices_the_current_complete_worst_ca"
            "se_tariff"
        ),
    ),
    ("app/simulation/planner.py", "_CLASS_ENTRY_AGE"): (
        (
            "tests/test_mutation38_simulation_backward_biology_instructio"
            "ns_v3.py::test_real_class_window_dates_the_birth_that_reache"
            "s_mid_class_at_sale"
        ),
    ),
    ("app/simulation/planner.py", "_CLASS_MAX_AGE"): (
        (
            "tests/test_mutation38_simulation_backward_biology_instructio"
            "ns_v3.py::test_real_class_window_dates_the_birth_that_reache"
            "s_mid_class_at_sale"
        ),
    ),
}

FUNCTION_ORACLES[("app/api/simulation.py", "farm_calibration")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/api/simulation.py", "farm_calibration"), ()))
        | {"tests/test_mutation38_simulation_calibration_evidence_admission_v2.py"}
    )
)

FUNCTION_ORACLES[("app/services/simulation_calibration.py", "_is_bakrid_month")] = tuple(
    sorted(
        set(
            FUNCTION_ORACLES.get(("app/services/simulation_calibration.py", "_is_bakrid_month"), ())
        )
        | {"tests/test_mutation38_simulation_calibration_evidence_admission_v2.py"}
    )
)

FUNCTION_ORACLES[("app/services/simulation_calibration.py", "_age_months")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/services/simulation_calibration.py", "_age_months"), ()))
        | {"tests/test_mutation38_simulation_calibration_evidence_admission_v2.py"}
    )
)

FUNCTION_ORACLES[("app/services/simulation_calibration.py", "_confidence")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/services/simulation_calibration.py", "_confidence"), ()))
        | {"tests/test_mutation38_simulation_calibration_evidence_admission_v2.py"}
    )
)

FUNCTION_ORACLES[("app/services/simulation_calibration.py", "_as_float")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/services/simulation_calibration.py", "_as_float"), ()))
        | {"tests/test_mutation38_simulation_calibration_evidence_admission_v2.py"}
    )
)

FUNCTION_ORACLES[("app/services/simulation_calibration.py", "_clamp")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/services/simulation_calibration.py", "_clamp"), ()))
        | {"tests/test_mutation38_simulation_calibration_evidence_admission_v2.py"}
    )
)

FUNCTION_ORACLES[("app/services/simulation_calibration.py", "_months_between")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/services/simulation_calibration.py", "_months_between"), ()))
        | {"tests/test_mutation38_simulation_calibration_evidence_admission_v2.py"}
    )
)

FUNCTION_ORACLES[("app/services/simulation_calibration.py", "_class_boundary")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/services/simulation_calibration.py", "_class_boundary"), ()))
        | {"tests/test_mutation38_simulation_calibration_evidence_admission_v2.py"}
    )
)

FUNCTION_ORACLES[("app/services/simulation_calibration.py", "_annual_fraction_from_exposure")] = (
    tuple(
        sorted(
            set(
                FUNCTION_ORACLES.get(
                    (
                        "app/services/simulation_calibration.py",
                        "_annual_fraction_from_exposure",
                    ),
                    (),
                )
            )
            | {"tests/test_mutation38_simulation_calibration_evidence_admission_v2.py"}
        )
    )
)

FUNCTION_ORACLES[("app/services/simulation_calibration.py", "_isotonic_fit")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/services/simulation_calibration.py", "_isotonic_fit"), ()))
        | {"tests/test_mutation38_simulation_calibration_evidence_admission_v2.py"}
    )
)

FUNCTION_ORACLES[("app/services/simulation_calibration.py", "_curve_rescale")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/services/simulation_calibration.py", "_curve_rescale"), ()))
        | {"tests/test_mutation38_simulation_calibration_evidence_admission_v2.py"}
    )
)

FUNCTION_ORACLES[("app/services/simulation_calibration.py", "_curve_from_observations")] = tuple(
    sorted(
        set(
            FUNCTION_ORACLES.get(
                ("app/services/simulation_calibration.py", "_curve_from_observations"),
                (),
            )
        )
        | {"tests/test_mutation38_simulation_calibration_evidence_admission_v2.py"}
    )
)

FUNCTION_ORACLES[("app/services/simulation_calibration.py", "calibrate_farm_assumptions")] = tuple(
    sorted(
        set(
            FUNCTION_ORACLES.get(
                (
                    "app/services/simulation_calibration.py",
                    "calibrate_farm_assumptions",
                ),
                (),
            )
        )
        | {"tests/test_mutation38_simulation_calibration_evidence_admission_v2.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/engine.py", "_run_core")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/engine.py", "_run_core"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/engine.py", "break_even_meat_price")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/engine.py", "break_even_meat_price"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/engine.py", "run_simulation")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/engine.py", "run_simulation"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/planner.py", "build_dpr_markdown")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/planner.py", "build_dpr_markdown"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/subsidy.py", "nlm_unit_subsidy_cap")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/subsidy.py", "nlm_unit_subsidy_cap"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "monthly_emi")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "monthly_emi"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "amortization_schedule")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "amortization_schedule"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "npv")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "npv"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "mirr")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "mirr"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "_power_sum")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "_power_sum"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "_opposite_float_signs")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "_opposite_float_signs"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "_sign_variations")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "_sign_variations"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "_normalise_power_terms")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "_normalise_power_terms"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "_bisect_power_sum")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "_bisect_power_sum"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "_decimal_normalise_power_terms")] = tuple(
    sorted(
        set(
            FUNCTION_ORACLES.get(
                ("app/simulation/finance.py", "_decimal_normalise_power_terms"), ()
            )
        )
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "_decimal_power_sum")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "_decimal_power_sum"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "_decimal_sign")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "_decimal_sign"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "_bisect_decimal_power_sum")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "_bisect_decimal_power_sum"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "_deduplicate_decimal_roots")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "_deduplicate_decimal_roots"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "_all_decimal_power_roots")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "_all_decimal_power_roots"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "_decimal_normalise_terms")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "_decimal_normalise_terms"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "_decimal_sign_variations")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "_decimal_sign_variations"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "_crossing_decimal_power_roots")] = tuple(
    sorted(
        set(
            FUNCTION_ORACLES.get(("app/simulation/finance.py", "_crossing_decimal_power_roots"), ())
        )
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "_exact_decimal_power_roots")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "_exact_decimal_power_roots"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "_has_integral_exponents")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "_has_integral_exponents"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "_scanned_power_roots")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "_scanned_power_roots"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "_positive_power_roots")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "_positive_power_roots"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "assess_irr")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "assess_irr"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "irr_roots")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "irr_roots"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "irr")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "irr"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "bcr")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "bcr"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/finance.py", "payback_month")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/finance.py", "payback_month"), ()))
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/services/tasks.py", "complete_task")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/services/tasks.py", "complete_task"), ()))
        | {
            "tests/test_mutation38_livestock_restored_zero_doe_weaning.py",
            "tests/test_mutation38_livestock_restored_zero_movements.py",
        }
    )
)


FUNCTION_ORACLES[("app/api/finance.py", "renew_policy")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/api/finance.py", "renew_policy"), ()))
        | {"tests/test_mutation38_livestock_insurance_retained_tag_consistency.py"}
    )
)
FUNCTION_ORACLES[("app/api/finance.py", "claim_policy")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/api/finance.py", "claim_policy"), ()))
        | {"tests/test_mutation38_livestock_insurance_retained_tag_consistency.py"}
    )
)


FUNCTION_ORACLES[("app/services/screening/pipeline.py", "_resolve_content_claim_conflict")] = tuple(
    sorted(
        set(
            FUNCTION_ORACLES.get(
                (
                    "app/services/screening/pipeline.py",
                    "_resolve_content_claim_conflict",
                ),
                (),
            )
        )
        | {
            "tests/test_mutation38_screening_content_claim_contracts.py",
            "tests/test_mutation38_screening_retention_cache_contracts.py",
        }
    )
)
FUNCTION_ORACLES[("app/services/screening/pipeline.py", "_reserve_normalized_content")] = tuple(
    sorted(
        set(
            FUNCTION_ORACLES.get(
                ("app/services/screening/pipeline.py", "_reserve_normalized_content"),
                (),
            )
        )
        | {
            "tests/test_mutation38_screening_content_claim_contracts.py",
            "tests/test_mutation38_screening_retention_cache_contracts.py",
        }
    )
)
FUNCTION_ORACLES[("app/api/screening.py", "request_upload")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/api/screening.py", "request_upload"), ()))
        | {"tests/test_mutation38_screening_upload_uuid_identity.py"}
    )
)
ASSIGNMENT_ORACLES[("app/services/screening/detect.py", "MIN_BOX_SIZE")] = tuple(
    sorted(
        set(ASSIGNMENT_ORACLES.get(("app/services/screening/detect.py", "MIN_BOX_SIZE"), ()))
        | {"tests/test_mutation38_detection_minimum_box_contracts.py"}
    )
)
FUNCTION_ORACLES[("app/services/screening/pipeline.py", "_claim_retry_rows")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/services/screening/pipeline.py", "_claim_retry_rows"), ()))
        | {
            "tests/test_mutation38_screening_queue_native_contracts.py",
            "tests/test_mutation38_screening_queue_cache_consistency_verified.py",
        }
    )
)
FUNCTION_ORACLES[("app/services/screening/pipeline.py", "run_screening_cycle")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/services/screening/pipeline.py", "run_screening_cycle"), ()))
        | {
            "tests/test_mutation38_screening_native_lease_preflight_contracts.py",
            "tests/test_mutation38_screening_queue_native_contracts.py",
            "tests/test_mutation38_screening_stale_peer_resume_contracts.py",
            "tests/test_mutation38_screening_queue_cache_consistency_verified.py",
            "tests/test_mutation38_screening_rollback_history_availability.py",
            "tests/test_mutation38_screening_preflight_query_failure_contracts.py",
        }
    )
)


FUNCTION_ORACLES[("app/simulation/engine.py", "_run_core")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/engine.py", "_run_core"), ()))
        | {"tests/test_mutation38_simulation_zero_stock_book_conservation.py"}
    )
)
FUNCTION_ORACLES[("app/simulation/engine.py", "run_simulation")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/engine.py", "run_simulation"), ()))
        | {"tests/test_mutation38_simulation_zero_stock_book_conservation.py"}
    )
)


FUNCTION_ORACLES[("app/simulation/engine.py", "_run_core")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/engine.py", "_run_core"), ()))
        | {"tests/test_mutation38_simulation_disabled_festival_holding_v1.py"}
    )
)
FUNCTION_ORACLES[("app/simulation/engine.py", "_is_festival_month")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/engine.py", "_is_festival_month"), ()))
        | {"tests/test_mutation38_simulation_disabled_festival_holding_v1.py"}
    )
)
FUNCTION_ORACLES[("app/simulation/engine.py", "_months_until_next_festival")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/engine.py", "_months_until_next_festival"), ()))
        | {"tests/test_mutation38_simulation_disabled_festival_holding_v1.py"}
    )
)
FUNCTION_ORACLES[("app/simulation/engine.py", "run_simulation")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/engine.py", "run_simulation"), ()))
        | {"tests/test_mutation38_simulation_disabled_festival_holding_v1.py"}
    )
)


FUNCTION_ORACLES[("app/simulation/explain.py", "_active_festival_months")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/explain.py", "_active_festival_months"), ()))
        | {"tests/test_mutation38_simulation_disabled_festival_narrative_v1.py"}
    )
)
FUNCTION_ORACLES[("app/simulation/explain.py", "_festival_paragraph")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/explain.py", "_festival_paragraph"), ()))
        | {"tests/test_mutation38_simulation_disabled_festival_narrative_v1.py"}
    )
)
FUNCTION_ORACLES[("app/simulation/explain.py", "_festival_figures")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/explain.py", "_festival_figures"), ()))
        | {"tests/test_mutation38_simulation_disabled_festival_narrative_v1.py"}
    )
)
FUNCTION_ORACLES[("app/simulation/explain.py", "build_narrative_report")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/explain.py", "build_narrative_report"), ()))
        | {"tests/test_mutation38_simulation_disabled_festival_narrative_v1.py"}
    )
)
FUNCTION_ORACLES[("app/simulation/engine.py", "run_simulation")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/engine.py", "run_simulation"), ()))
        | {"tests/test_mutation38_simulation_disabled_festival_narrative_v1.py"}
    )
)


FUNCTION_ORACLES[("app/simulation/montecarlo.py", "_triangular_from_uniform")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/montecarlo.py", "_triangular_from_uniform"), ()))
        | {"tests/test_mutation38_monte_carlo_triangular_mode_v1.py"}
    )
)
FUNCTION_ORACLES[("app/simulation/montecarlo.py", "_correlated_draws")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/montecarlo.py", "_correlated_draws"), ()))
        | {"tests/test_mutation38_monte_carlo_triangular_mode_v1.py"}
    )
)


# Additional reviewed native control cases for the next fresh campaign.
FUNCTION_ORACLES[("app/api/simulation.py", "farm_calibration")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/api/simulation.py", "farm_calibration"), ()))
        | {"tests/test_mutation38_simulation_calibration_age_zero_live_native_v1.py"}
    )
)

FUNCTION_ORACLES[("app/services/simulation_calibration.py", "calibrate_farm_assumptions")] = tuple(
    sorted(
        set(
            FUNCTION_ORACLES.get(
                (
                    "app/services/simulation_calibration.py",
                    "calibrate_farm_assumptions",
                ),
                (),
            )
        )
        | {"tests/test_mutation38_simulation_calibration_age_zero_live_native_v1.py"}
    )
)

FUNCTION_ORACLES[("app/services/simulation_calibration.py", "_curve_from_observations")] = tuple(
    sorted(
        set(
            FUNCTION_ORACLES.get(
                ("app/services/simulation_calibration.py", "_curve_from_observations"),
                (),
            )
        )
        | {"tests/test_mutation38_simulation_calibration_age_zero_live_native_v1.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/daily_ops.py", "_build_notes")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/daily_ops.py", "_build_notes"), ()))
        | {"tests/test_mutation38_simulation_daily_sire_availability_v2.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/daily_ops.py", "_mortality")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/daily_ops.py", "_mortality"), ()))
        | {"tests/test_mutation38_simulation_daily_young_stock_mortality_boundary_v1.py"}
    )
)

ASSIGNMENT_ORACLES[("app/simulation/finance.py", "_DECIMAL_ISOLATION_MAX_TERMS")] = tuple(
    sorted(
        set(
            ASSIGNMENT_ORACLES.get(
                ("app/simulation/finance.py", "_DECIMAL_ISOLATION_MAX_TERMS"), ()
            )
        )
        | {"tests/test_mutation38_simulation_financial_appraisal_native_v4.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/engine.py", "_run_core")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/engine.py", "_run_core"), ()))
        | {"tests/test_mutation38_simulation_ordered_grower_event_conservation_v2.py"}
    )
)

FUNCTION_ORACLES[("app/simulation/engine.py", "run_simulation")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/simulation/engine.py", "run_simulation"), ()))
        | {"tests/test_mutation38_simulation_ordered_grower_event_conservation_v2.py"}
    )
)


FUNCTION_ORACLES[("app/api/dashboard.py", "dashboard")] = tuple(
    sorted(
        set(FUNCTION_ORACLES.get(("app/api/dashboard.py", "dashboard"), ()))
        | {"tests/test_mutation38_dashboard_concurrent_hold_native_contract_v3.py"}
    )
)


def select_staged_contracts(file: str, tree: ast.Module, line: int) -> list[str]:
    selected: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ) or not node.lineno <= line <= (node.end_lineno or node.lineno):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            selected.update(FUNCTION_ORACLES.get((file, node.name), ()))
        elif isinstance(node, ast.ClassDef):
            selected.update(CLASS_ORACLES.get((file, node.name), ()))
            for member in node.body:
                if isinstance(member, (ast.Assign, ast.AnnAssign)) and member.lineno <= line <= (
                    member.end_lineno or member.lineno
                ):
                    targets = member.targets if isinstance(member, ast.Assign) else [member.target]
                    for target in targets:
                        if isinstance(target, ast.Name):
                            selected.update(FIELD_ORACLES.get((file, node.name, target.id), ()))
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.lineno <= line <= (
            node.end_lineno or node.lineno
        ):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    selected.update(ASSIGNMENT_ORACLES.get((file, target.id), ()))
    return sorted(selected)
