# テスト

実行: `python3 -m pytest -q`（ダミーデータを自動生成。`-m "not slow"` で短縮）。

## 保存データ単体での実行結果

保存データのソース（source/）だけを別の一時フォルダに展開し、`PYTHONPATH=src python3 -m pytest -q -m "not slow"` を実行した結果の末尾:

```text
........................................................................ [ 32%]
........................................................................ [ 64%]
...............................................................s........ [ 97%]
......                                                                   [100%]
=============================== warnings summary ===============================
tests/test_reading.py::test_lossless_upload_keeps_pixels
tests/test_reading.py::test_lossless_upload_keeps_pixels
  /tmp/tmpoa_cb6l6/source/tests/test_reading.py:582: DeprecationWarning: Image.Image.getdata is deprecated and will be removed in Pillow 14 (2027-10-15). Use get_flattened_data instead.
    assert media == "image/webp" and list(got.getdata()) == list(Image.open(f).convert("RGB").getdata())

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
221 passed, 1 skipped, 5 deselected, 2 warnings in 350.29s (0:05:50)
```

## テストの一覧

### `tests/test_check_catalog.py`

チェック項目の全体設計（カタログ）と実装の一致、および全体設計で追加した各項目。

- `test_catalog_structure` — 
- `test_design_items_are_backed_by_existing_tests` — 
- `test_every_recorded_check_is_designed_and_core_items_run` — 
- `test_unit_coverage_detects_gap` — 
- `test_inference_never_gets_teacher_speaker` — 
- `test_table_and_chart_reference_checks` — 
- `test_style_pdf_not_in_repository` — 本人の文章スタイルPDFとその読み取り結果はリポジトリに含めない。
- `test_style_checks_are_recorded` — 

### `tests/test_checks.py`

各工程に組み込んだチェックと、独立した再検証・提出物の下書きチェック。

- `test_formula_evaluator` — 
- `test_formula_unsupported_and_errors` — 
- `test_excel_recalc_ok_mismatch_unverified` — 
- `test_text_quality_helpers` — 
- `test_extraction_checks_in_pipeline` — 
- `test_no_speech_with_audio_needs_review` — 
- `test_understanding_and_directive_checks` — 
- `test_speaker_confirmed_by_user` — 
- `test_ai_teacher_claim_is_not_confirmed` — 
- `test_integration_numeric_and_submission` — 
- `test_reverify_independent_and_not_overwriting` — 
- `test_check_draft_text_and_excel` — 

### `tests/test_cli_and_sync.py`

CLI と、使い捨て環境間での再開（state.db を出力先に保存→別環境で取得）。

- `test_resume_in_fresh_container_via_saved_state` — 
- `test_cli_status_report_errors` — 
- `test_check_without_credentials` — 
- `test_concurrent_run_is_blocked` — 

### `tests/test_config_and_integrations.py`

設定・Dropbox クライアント・Anthropic プロバイダ（いずれも通信なしの模擬で確認）。

- `test_secrets_in_config_rejected` — 
- `test_output_inside_source_rejected` — 
- `test_example_config_loads` — 
- `test_dropbox_listing_recursive_with_pagination` — 
- `test_dropbox_source_has_no_write_methods` — 
- `test_dropbox_output_restricted_to_output_folder` — 
- `test_anthropic_request_shape` — 
- `test_anthropic_refusal_and_bad_json` — 
- `test_batch_cache_priming_changes_only_cache_settings_and_order` — 同じ先頭部分（長い指示文）を持つ依頼が多いときだけ、1件を先に送ってキャッシュを書き（1 時間）、残りを後で送る。
- `test_batch_priming_sends_first_phase_before_the_rest` — 

### `tests/test_connect.py`

接続確認・認証情報の扱い・ワークフロー（いずれも通信なしの模擬で確認）。秘密の値が出力に出ないことも確認する。

- `test_credentials_from_env_and_file` — 
- `test_redact_hides_all_secret_values` — 
- `test_connect_check_lists_downloads_and_never_prints_secrets` — 
- `test_connect_check_folder_missing_and_bad_key` — 
- `test_connect_check_without_credentials` — 
- `test_dropbox_auth_never_prints_token` — 
- `test_connection_check_workflow_is_readonly_and_masks` — 
- `test_analyze_workflow_ai_needs_explicit_request` — 
- `test_oauth_diagnosis_pinpoints_cause` — 
- `test_exchange_then_store_in_github_secrets` — 
- `test_exchange_expired_code_and_bad_pat` — 
- `test_preflight_writes_and_removes_test_secret` — 
- `test_dropbox_auth_workflow_order_and_secret_scoping` — 
- `test_token_store_exchange_needs_only_stdlib` — 

### `tests/test_costmodel.py`

費用モデル（structlab cost）。API は呼ばない。

- `test_prefix_units_priming_pays_off_only_for_large_groups` — 
- `test_semantic_cost_after_m24_is_lower_and_candidates_are_reported` — 
- `test_actions_usage_rounds_each_job_up_to_minutes` — 

### `tests/test_formats.py`

PDF・画像・Excel・テキスト・Office の抽出内容。

- `test_pdf_text_tables_formulas_figures` — 
- `test_excel_cells_formulas_charts` — 
- `test_excel_macro_flag` — 
- `test_image_ocr_and_ai_image` — 
- `test_heic_photo` — 
- `test_legacy_and_unknown_formats_recorded` — 
- `test_text_cp932` — 
- `test_pptx_minimal` — 

### `tests/test_large_files.py`

大容量ファイルの分割処理（メモリに一括で載せないこと）。

- `test_hash_of_large_file_uses_little_memory` — 
- `test_dropbox_download_is_streamed_and_verified` — 
- `test_dropbox_download_hash_mismatch_detected` — 
- `test_large_pdf_is_split_per_page` — 
- `test_large_excel_is_split_by_rows` — 
- `test_long_video_is_split_into_segments` — 

### `tests/test_pilot.py`

費用を下げる候補の品質比較（採用の判断）。

- `test_candidate_adopted_only_when_as_good_as_baseline_rerun` — 
- `test_reading_items_catch_numbers_cells_formulas` — 
- `test_branch_pilot_reruns_only_the_verify_stage` — 本番を照合の直前まで進め、見本の資料だけ DB を複製して照合から先を方式ごとに実行する（Mock）。
- `test_branch_compare_reuses_baseline_as_production` — 比較の今の方式の 1 回目は本番の結果に使う: 本番を再開しても見本の資料の照合は送り直さない。
- `test_reinterpret_delta_candidate_rebuilds_the_same_analysis` — 候補C5（再解釈で変わった項目だけ返す）: 保存される場面の解釈・変更点・指示は、全体を書き直させる今の方式と同じになる（Mock）。

### `tests/test_provenance_and_report.py`

解析結果の出典管理・先生の指示とAI推測の区別・10分類の出力。

- `test_every_item_has_source` — 
- `test_ai_inference_is_never_instructor_instruction` — 
- `test_ai_output_validation` — 
- `test_knowledge_outputs` — 
- `test_local_only_mode_marks_gaps` — 
- `test_external_ai_requires_explicit_permission` — 

### `tests/test_queue.py`

候補 G5: Claude Code で解析するための依頼の受け渡し（API の依頼と同じ内容・同じ出力の形）。

- `test_queue_round_trip_resumes_and_validates` — 
- `test_semantic_layer_through_queue_matches_direct_answers` — 意味理解を受け渡し口で実行し、各依頼に（Mock の）同じ答えを書き戻すと、直接答えた場合と同じ指示の一覧になる。
- `test_large_images_get_full_resolution_tiles` — Claude Code の Read は長辺 2000 を超える画像を縮小するので、API と同じ画素を部分画像で添える。
- `test_queue_abnormal_cases` — 異常系: 形の違う答え・壊れた JSON・重複した答え・依頼の無い答え・壊れた answers・依頼の変更・画像の欠落。
- `test_g5_driver_guards_and_assignment` — G5 の実行役: 有料の API の設定では動かない、受け渡し中の依頼を二重に渡さない、テスト用の答えでは完了にしない。
- `test_request_record_rebuilds_the_identical_api_request` — 見本の比較で API に送る依頼は、本番で API に送る依頼と同じ（指紋が同じ）。
- `test_pg5_select_run_and_compare` — P-G5: 種類ごとの見本を選び、API（ここでは Mock）で 2 回答えさせ、G5 の答えと比べる。
- `test_request_markdown_is_complete_and_line_split` — 答え役が読む依頼の Markdown: 指示・画像・本文・出力の形がすべて入り、長い 1 行が無く、終わりの印がある。
- `test_review_fixes_tamper_wrapper_thin_tiles` — 
- `test_fake_answers_taint_the_work_folder_forever` — 
- `test_broken_images_are_rebuilt` — 途中で止まって壊れた画像（拡大画像・依頼の画像）は使わず作り直す。

### `tests/test_reading.py`

資料読み取り基盤（structlab read）: 棚卸し・PDFの位置付き読み取り・画面録画の領域検出と場面分割・照合・AI書き起こしの流れ。

- `test_inventory_duplicates_and_helpers` — 
- `test_pdf_rows_follow_displayed_layout` — 
- `test_screen_recording_region_scenes_and_revisits` — 
- `test_verify_pdf_page_coverage_and_numbers` — 
- `test_scene_links_to_pdf_page_and_is_checked_against_it` — 
- `test_run_reading_end_to_end` — 
- `test_estimate_counts_unique_scenes_only` — 
- `test_speech_corrections_need_support_in_materials` — 
- `test_merge_counts_pdf_once` — 
- `test_list_targets_one_job_per_video` — 
- `test_reading_workflow_is_readonly_and_ai_gated` — 
- `test_crop_survives_resolution_change` — 録画の途中で画面の向き・大きさが変わっても、切り出しで失敗しない（実際の画面録画で発生した不具合）。
- `test_stream_seconds_reads_mkv_duration_tag` — 
- `test_trim_only_when_returning_to_opening_and_silent` — 
- `test_role_matching_and_teacher_timeline_is_not_filled` — 
- `test_content_box_finds_video_inside_browser_player` — 
- `test_orientation_runs_detect_sideways_screen_and_ignore_blips` — 
- `test_frame_size_native_and_detail` — 
- `test_experiment_windows_are_read_one_by_one_by_default` — 区間をまとめて読む方法は品質の確認ができていないので既定では使わない（従来どおり区間ごと）。
- `test_reference_image_is_cached_without_changing_the_request` — 
- `test_crop_is_safe_detects_content_outside` — 
- `test_grouped_experiment_request_shares_reference` — 
- `test_batch_mode_collects_then_reads_saved_results` — 
- `test_detail_keeps_every_distinct_change` — 
- `test_detail_images_zoom_only_where_needed` — 
- `test_detail_requests_split_zoom_pass_and_merge` — 
- `test_lossless_upload_keeps_pixels` — 
- `test_detail_mode_saves_native_lossless_frames_and_brief_displays` — Excel・レポートの作り方: 元の解像度の可逆画像で保存し、0.4 秒だけ出た画面も拾う。
- `test_semantic_bridge_reuses_reading_results` — 読み取り結果を意味理解層の場面にする（120 秒ごと・除外した末尾を含めない・画像は第3回の照合だけ）。
- `test_semantic_bridge_batches_ai_calls_without_deadlock` — 資料ごとの意味理解（第1〜3回）を並行に進め、AI の呼び出しを Batch でまとめて送る（Mock で確認）。
- `test_detail_estimate_does_not_write_images` — 
- `test_saved_ai_result_is_reused_only_for_the_identical_request` — 保存済みの AI の結果は、今送るのと全く同じ依頼（本文・指示・画像の中身）のときだけ使い直す。
- `test_compact_material_keeps_every_key_and_value` — 候補「短い書き方」: すべてのキー・値（数値・式・記号・空の値）が順序どおりに残る。
- `test_candidate_options_keep_results_with_mock` — 候補（照合のまとめ・先頭配置・短い書き方）を入れても、Mock では同じ指示・同じ判定になる（仕組みの確認。
- `test_independent_requests_are_sent_together_with_identical_content` — 指示の抽出・照合をまとめて送っても、AI に送る依頼の全文（指示文・本文・画像・スキーマ・キャッシュの境目）と
- `test_without_batch_gate_each_result_is_saved_before_the_next_call` — Batch でまとめない経路（structlab run など）では従来どおり 1 件ずつ送ってすぐ保存する。
- `test_interrupted_semantic_batch_is_fetched_not_sent_again` — 意味理解の Batch を送った後に中断しても、再実行では同じ依頼の集まりなら送信済みの Batch の結果を取りに行く。

### `tests/test_recovery.py`

読み込み失敗時の記録・再試行・復旧。

- `test_corrupt_file_recorded_and_retried_up_to_limit` — 
- `test_transient_unit_failure_recovers_next_run` — 
- `test_ai_failure_is_retried_on_next_run` — 
- `test_ai_budget_limit_defers_remaining_units` — 
- `test_retry_command_resets_failures` — 

### `tests/test_requirements.py`

要求事項の台帳（利用者の指示）と照合の仕組み。

- `test_process_registry_is_complete_and_keeps_conflicts` — 
- `test_stage_recheck_never_marks_success_without_evidence` — 
- `test_teacher_directives_enter_the_ledger_with_sources` — 

### `tests/test_resume_and_detection.py`

途中停止と再開・新規ファイル検出・重複処理の防止・更新の再解析・アップロード途中の除外。

- `test_stop_and_resume_without_rework` — 
- `test_crash_recovery_resets_running_units` — 
- `test_max_runtime_stops_gracefully` — 
- `test_second_run_does_nothing` — 
- `test_new_file_detected_later` — 
- `test_identical_copy_is_not_reanalyzed` — 
- `test_updated_file_is_reanalyzed_and_old_results_hidden` — 
- `test_deleted_file_is_excluded` — 
- `test_files_still_uploading_are_not_analyzed` — 
- `test_file_changed_between_scan_and_fetch` — 

### `tests/test_safety.py`

ZIP爆弾・危険パス・ディスク容量・ハッシュ計算。

- `test_dangerous_member_paths_rejected` — 
- `test_safe_member_path_normalizes` — 
- `test_zip_traversal_not_extracted` — 
- `test_zip_symlink_not_extracted` — 
- `test_zip_bomb_ratio_detected` — 
- `test_zip_total_size_limit` — 
- `test_zip_file_count_limit` — 
- `test_cp932_zip_names` — 
- `test_disk_space_check` — 
- `test_dropbox_hash_streaming_matches` — 
- `test_dropbox_hash_known_empty` — 

### `tests/test_semantic.py`

意味理解層: 局所理解・全体理解・相互照合・指示の網羅的抽出と分類・照合・資料横断・網羅率。

- `test_rule_classes` — 
- `test_rule_condition_and_base_strength` — 
- `test_rule_discourse_carries_to_next_instruction` — 
- `test_te_form_split_is_marked_uncertain` — 
- `test_target_similarity` — 
- `test_local_mode_extracts_directives_with_evidence` — 
- `test_local_mode_reports_and_coverage` — 
- `test_untranscribed_audio_shows_in_coverage` — 
- `test_three_stages_in_order_with_context` — 
- `test_local_global_reconciliation_loop` — 
- `test_omission_and_misclassification_are_caught` — 
- `test_directive_record_has_all_fields` — 
- `test_corpus_integration_is_separate_and_validated` — 
- `test_second_ai_run_makes_no_new_calls` — 
- `test_budget_stop_and_resume_without_redoing` — 
- `test_failed_scene_is_reported_not_hidden` — 
- `test_revision_that_flips_strength_is_not_accepted` — 
- `test_replace_unit_analysis_avoids_double_ai` — 
- `test_long_document_synthesis_is_split_not_truncated` — 
- `test_ai_claimed_correction_without_marker_stays_unresolved` — 
- `test_conflict_without_correction_is_recorded_not_resolved` — 
- `test_estimate_before_paid_run_calls_no_api` — 
- `test_schemas_are_valid_and_mock_outputs_conform` — 出力スキーマ自体の妥当性と、各タスクの応答がスキーマに沿うこと（実APIでの受理は未検証）。

### `tests/test_style_feedback.py`

文章スタイル（最終工程のみ）と、フィードバックの記録・工程の追跡。

- `test_parse_profile_and_exclusions` — 
- `test_real_style_pdf` — 
- `test_verify_preservation` — 
- `test_style_disabled_or_missing_pdf_does_not_stop` — 
- `test_style_apply_and_rejects_content_changes` — 
- `test_style_markdown_keeps_tables_and_writes_original` — 
- `test_style_is_isolated_from_analysis` — 文章スタイルは最終工程専用: 意味理解・指示判断・計算・チェックのコードから参照しない。
- `test_pipeline_runs_with_style_enabled_but_no_pdf` — 
- `test_feedback_and_trace` — 

### `tests/test_video.py`

動画の音声と映像の対応付け・重要場面の追加解析。

- `test_align_assigns_frame_shown_at_speech_start` — 
- `test_align_keeps_frames_without_speech` — 
- `test_pick_keyframes_limit_keeps_first_and_largest` — 
- `test_focus_spec_is_clamped_to_duration` — 
- `test_video_speech_and_screen_alignment_end_to_end` — 
- `test_audio_only_file` — 
- `test_truncated_video_is_reported` — 
- `test_real_transcription_with_faster_whisper` — faster-whisper と音声合成(espeak-ng)がある環境のみ。実音声での文字起こし配線を確認する。
