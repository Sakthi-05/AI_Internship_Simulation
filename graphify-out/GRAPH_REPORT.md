# Graph Report - AI_Internship_Simulation_Final  (2026-11-17)

## Corpus Check
- 342 files · ~191,777 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 23 file(s) not represented in the graph (top: .zip 7, (none) 5, .log 4)

## Summary
- 3523 nodes · 11683 edges · 177 communities (112 shown, 65 thin omitted)
- Extraction: 85% EXTRACTED · 15% INFERRED · 0% AMBIGUOUS · INFERRED: 1762 edges (avg confidence: 0.94)
- Token cost: 1,800 input · 1,250 output

## Community Hubs (Navigation)
- Assessment: Adaptive Technical
- Agent: Task Downgrade
- Simulation: Task Sprint
- Agent: Feature Session
- Agent: Review Task
- Submissions: Workflow Task
- Simulation: Manager Message
- Submissions: Submission Task
- Agent: Manager Agent
- Agent: Agent .execute
- Agent: Agent.py Agent
- Submissions: Chunk Must
- Submissions: Generate Review
- Assessment: Review Student
- Simulation: Chatthread App.jsx
- Agent: Scenario Agent
- Submissions: Exception Handler
- Assessment: Question Snapshot
- Testing: Package / Module1-Registration.Spec
- Agent: Adaptation Agent
- Assessment: Domain Ratings
- Simulation: .test Tasks
- Simulation: Simulation Task
- Assessment: Skills Profile
- Assessment: Requirement Change
- Project Catalog / Project Generator Module
- Security: Environment Ports
- Agent: Review Technical
- Simulation: Task Verbs
- Security: Profile Returns
- Simulation: Workspace Ref
- Simulation: Task Start
- Submissions: Types Codeeditor.jsx
- Submissions: Review Resubmission
- Assessment: Baseline Question
- Assessment: Fallback Question
- Agent: Feature Features
- Simulation: Task .test
- Simulation: Task Path
- Simulation: Task Message
- Simulation: Task Review
- Assessment: Downgrade Upgrade
- Security: Student Register
- Simulation: Step Ambientbackground.jsx
- Agent: Competency Evidence
- Task: Criteria Version
- Assessment: Question Admin
- Simulation: Requirement Change
- Simulation: Scenario Status
- Testing: Technical Domain Catalog / Test Technical Domain Catalog
- Agent: Task Focus
- Simulation: Manager Generate
- Submissions: File Path
- Simulation: Path Task
- Assessment: Service Dynamic
- Assessment: Student Profile
- Testing: Test Gemini Overload Retry
- Submissions: Review Prompt
- Simulation: Simulation Test:
- Security: Feature Websocket
- Simulation: .generate Gemini
- Assessment: Client.js Apiclient
- Task: Meta Status
- Agent: Context Contracts
- Deps / Companies Module
- Testing: Test Review Provider Validation / Ai Service
- Simulation: Simulation Task (66)
- Submissions: Calculate Next
- Agent: Criteria Event
- Agent: Analytics Simulation
- Misc / Meetings Module
- Assessment: Assessmenthistory.jsx Assessmentfeedback
- Submissions: Context Build
- Security: Settings Dev
- Ai Service Module
- Submissions: Task Test:
- Submissions: Extract Content
- Simulation: Task Create
- Simulation: Create Student
- Analytics: Route Activity
- Submissions: That Invalid
- Assessment: Fallback Questions
- Simulation: Postgres Database
- Simulation: Task Dynamic
- Assessment: Workplacescenario.js Activerequirementchange
- Assessment: Auth Headers
- Submissions: Submit Simulation
- Simulation: .test Simulation
- Simulation: Simulation Auth
- Simulation: Check Fastapi
- Testing: Events
- Projects / Project Module
- Simulation: Sprints.py Assert
- Task: Provider Review
- Submissions: Results Research
- Simulation: Manager Profile
- Assessment: Snapshot Attempt
- Notifications Module
- Team Module
- Content Extraction Module
- Testing: Workplace Scenarios / Test Workplace Requirement Change
- Simulation: Database Workflow
- Assessment: Projection Result
- Simulation: Transition Simulation
- Testing: Test Review Route Orchestration
- Simulation: Local Included
- Testing: Test Scenario Progression Regression
- Testing: Test Gemini Optional
- Agent: .execute Result
- Simulation: Module Contract
- Simulation: Phase Verification
- Manager Module
- UI: .Oxlintrc
- Testing: Test Review Provider Validation
- Simulation: Integrating With
- Simulation: Flow Admin
- Simulation: Test: Access
- Criteria Version Service / Workplace Scenarios Module
- Workplace Scenarios Module
- Simulation: Container Image
- Simulation: Adaptive Intelligence
- Simulation: React Expanding
- Simulation: Setup Apply
- Submissions: Countingreviewservice .review
- Task: Client Fixture
- Simulation: Test: Post
- Simulation: Start Task
- Simulation: Complete Test:
- Simulation: Simulation Test: (132)
- Submissions: Submission Deadline
- Simulation: Time Tracking
- Submissions: Build Submission
- Content Extraction Module (136)

## God Nodes (most connected - your core abstractions)
1. `Task` - 191 edges
2. `Simulation` - 160 edges
3. `CurrentUser` - 136 edges
4. `Sprint` - 114 edges
5. `TaskStatus` - 105 edges
6. `Project` - 102 edges
7. `AIService` - 80 edges
8. `Base` - 78 edges
9. `Company` - 76 edges
10. `WorkflowEvent` - 75 edges

## Surprising Connections (you probably didn't know these)
- `_setup_database()` --uses--> `Base`  [INFERRED]
  tests/conftest.py → backend/app/core/database.py
- `setup_database()` --uses--> `Base`  [INFERRED]
  tests/test_e2e_demo.py → backend/app/core/database.py
- `_setup_database()` --uses--> `Base`  [INFERRED]
  tests/test_member2_feature2_task_paths.py → backend/app/core/database.py
- `_setup_database()` --uses--> `Base`  [INFERRED]
  tests/test_member2_feature3_start_flow.py → backend/app/core/database.py
- `_setup_database()` --uses--> `Base`  [INFERRED]
  tests/test_project_sprint_task_hierarchy.py → backend/app/core/database.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **AI Internship System Core Specifications** — docs_architecture_system_architecture, docs_api_api_reference, docs_database_schema_database_schema [INFERRED 0.85]

## Communities (177 total, 65 thin omitted)

### Community 0 - "Assessment: Adaptive Technical"
Cohesion: 0.06
Nodes (86): adaptive_history(), AdaptiveResponse, admin_students(), AssessmentRequest, certificate_issue(), certificate_print(), certificates(), feedback() (+78 more)

### Community 1 - "Agent: Task Downgrade"
Cohesion: 0.07
Nodes (36): downgrade(), upgrade(), upgrade(), upgrade(), upgrade(), upgrade(), upgrade(), upgrade() (+28 more)

### Community 2 - "Simulation: Task Sprint"
Cohesion: 0.06
Nodes (41): BugReportRequest, _check_dependencies(), _get_company_id_for_task(), get_kanban_board(), _get_owned_task(), get_task(), report_bug(), _sync_project_state() (+33 more)

### Community 3 - "Agent: Feature Session"
Cohesion: 0.06
Nodes (34): _build_internship(), _find_generation_request(), generate_internship_simulation(), _generation_response(), _resolve_generation_inputs(), AIPersona, CompanyType, DifficultyLevel (+26 more)

### Community 4 - "Agent: Review Task"
Cohesion: 0.08
Nodes (35): create_manager_review(), create_review(), _get_owned_project(), _get_owned_version(), get_review_history(), get_review_status(), ReviewResult, ReviewSeverity (+27 more)

### Community 5 - "Submissions: Workflow Task"
Cohesion: 0.08
Nodes (37): _link_workplace_scenario(), _calculate_simulation_progress(), get_current_task(), _sync_current_scenario_from_task(), ScenarioStatus, SimulationStatus, WorkflowEventType, Scenario (+29 more)

### Community 6 - "Simulation: Manager Message"
Cohesion: 0.06
Nodes (40): get_manager_chat_history(), _get_owned_company(), get_team_chat_history(), _project_for(), send_manager_message(), send_team_message(), respond_to_simulation_event(), MessageSenderType (+32 more)

### Community 7 - "Submissions: Submission Task"
Cohesion: 0.08
Nodes (26): submit_simulation_task(), create_submission(), _get_owned_project(), _get_owned_submission(), _get_owned_task(), _get_owned_version(), get_submission(), get_submission_version() (+18 more)

### Community 8 - "Agent: Manager Agent"
Cohesion: 0.08
Nodes (34): bind_manager_runtime(), _clean_response(), create_manager_orchestrator(), _fallback_message(), invoke_manager_agent(), ManagerAgent, ManagerAgentInput, ManagerAgentOutput (+26 more)

### Community 9 - "Agent: Agent .execute"
Cohesion: 0.07
Nodes (27): output_mapping(), AgentErrorInfo, AgentResponse, ExecutionMetadata, ExecutionStatus, get_agent_orchestrator(), AgentOrchestrator, AgentRegistry (+19 more)

### Community 10 - "Agent: Agent.py Agent"
Cohesion: 0.07
Nodes (23): BaseAgent, EvidenceReference, AdaptationAgent, AdaptationAgentOutput, AdaptationContextError, _unique_refs(), CareerInsightsAgent, CareerInsightsInput (+15 more)

### Community 11 - "Submissions: Chunk Must"
Cohesion: 0.06
Nodes (31): make_chunk(), send_chunk(), test_assemble_file_fails_when_chunk_is_missing(), test_assemble_file_preserves_chunk_order(), test_checksum_mismatch_is_rejected(), test_client(), test_concurrent_chunk_uploads(), test_conflicting_duplicate_chunk_is_rejected() (+23 more)

### Community 12 - "Submissions: Generate Review"
Cohesion: 0.05
Nodes (21): get_ai_service(), _clean_str_list(), shape_review_feedback(), _DemandingReviewer, _generate(), _start_task(), _submit_task_version(), test_ai_failure_uses_deterministic_review_and_resolves_state() (+13 more)

### Community 13 - "Assessment: Review Student"
Cohesion: 0.08
Nodes (32): AcceptanceCriterionReview, AIManagerDecision, AIReviewResponse, CodeQualityReview, PerformanceReview, SecurityReview, TestingReview, test() (+24 more)

### Community 14 - "Simulation: Chatthread App.jsx"
Cohesion: 0.10
Nodes (40): App(), EmptyState(), Layout(), TABS, NotificationsBell(), RequireAdmin(), ChatThread(), formatTime() (+32 more)

### Community 15 - "Agent: Scenario Agent"
Cohesion: 0.10
Nodes (24): AgentContext, create_scenario_orchestrator(), _generation_prompt(), register_scenario_agent(), ScenarioAgent, ScenarioAgentInput, ScenarioAgentOutput, ScenarioCategory (+16 more)

### Community 16 - "Submissions: Exception Handler"
Cohesion: 0.05
Nodes (15): AIError, AppException, DatabaseError, DependencyBlockedError, DuplicateSubmissionError, FileError, ForbiddenError, InvalidStateError (+7 more)

### Community 17 - "Assessment: Question Snapshot"
Cohesion: 0.09
Nodes (34): AssessmentQuestion, Skill, _admin_question(), _attempt_payload(), _build_assessment_snapshot(), _contains_phrase(), create_question(), _create_snapshot_attempt() (+26 more)

### Community 18 - "Testing: Package / Module1-Registration.Spec"
Cohesion: 0.05
Nodes (48): dependencies, axios@^1.19.0, date-fns@^4.4.0, @dnd-kit/core@^6.3.1, @dnd-kit/sortable@^10.0.0, @dnd-kit/utilities@^3.2.2, framer-motion@^12.43.0, react@^19.2.8 (+40 more)

### Community 19 - "Agent: Adaptation Agent"
Cohesion: 0.08
Nodes (29): AgentRequest, AdaptationAgentInput, bind_adaptation_runtime(), invoke_adaptation_agent(), register_adaptation_agent(), ContextRouter, _immediate_wait_for(), make_case() (+21 more)

### Community 20 - "Assessment: Domain Ratings"
Cohesion: 0.08
Nodes (23): StudentTechnicalTechnologyRating, _rating_upsert_statement(), save_technical_domain_ratings(), _persist(), TechnicalProfileValidationError, validate_technical_domain_ratings(), _FakeSession, _ratings() (+15 more)

### Community 21 - "Simulation: .test Tasks"
Cohesion: 0.07
Nodes (15): auth_headers(), client(), _create_company_and_project(), _generate(), make_token(), other_auth_headers(), _seed_project_with_sprints_and_tasks(), _setup_database() (+7 more)

### Community 22 - "Simulation: Simulation Task"
Cohesion: 0.09
Nodes (23): acknowledge_simulation_event(), _check_task_unlocked(), complete_simulation_task(), create_simulation(), CreateSimulationRequest, _format_remaining_time(), get_current_scenario(), _get_owned_simulation() (+15 more)

### Community 23 - "Assessment: Skills Profile"
Cohesion: 0.08
Nodes (23): my_profile(), my_recommendation(), my_technical_profile(), _profile_view(), ProfileUpdate, SkillRating, skills(), SkillUpdate (+15 more)

### Community 24 - "Assessment: Requirement Change"
Cohesion: 0.18
Nodes (30): WorkflowEventStatus, WorkflowEvent, acknowledge_requirement_change(), _adaptability_observation(), assess_requirement_change(), assess_requirement_changes_for_submission(), assessment_for_event(), _assessment_payload() (+22 more)

### Community 25 - "Project Catalog / Project Generator Module"
Cohesion: 0.11
Nodes (26): _catalog_difficulty_label(), CatalogModule, CatalogProject, _filter_candidates(), _load_catalog_file(), load_projects_for_company_type(), _matches_difficulty(), NoMatchingProjectError (+18 more)

### Community 26 - "Security: Environment Ports"
Cohesion: 0.05
Nodes (42): VITE_API_BASE_URL, build, depends_on, environment, ports, restart, args, context (+34 more)

### Community 27 - "Agent: Review Technical"
Cohesion: 0.09
Nodes (21): bind_technical_review_runtime(), create_technical_review_orchestrator(), _criteria_provenance(), register_technical_review_agent(), TechnicalReviewAgent, TechnicalReviewContextError, TechnicalReviewInput, TechnicalReviewOutput (+13 more)

### Community 28 - "Simulation: Task Verbs"
Cohesion: 0.09
Nodes (17): _append_adaptive_tasks(), _add(), _assign_deadline(), _assign_module_dependencies(), _assign_priority(), _build_module_plans(), _classify_task_type(), _create_default_sprints() (+9 more)

### Community 29 - "Security: Profile Returns"
Cohesion: 0.09
Nodes (21): update_my_technical_profile(), get_student_technical_profile(), _payload(), _ratings(), _ReadResult, _RouteSession, _TechnicalProfileReadSession, test_auth_dependency_rejects_unauthenticated_access() (+13 more)

### Community 30 - "Simulation: Workspace Ref"
Cohesion: 0.12
Nodes (28): ProgressIndicator(), ScenarioProgressTab(), ScenarioPanel(), ScenarioTaskItem(), useAuth(), useCompanyWebSocket(), WS_BASE, refreshWorkplace() (+20 more)

### Community 31 - "Simulation: Task Start"
Cohesion: 0.09
Nodes (11): client(), _full_setup(), _headers(), make_token(), _seed_company(), _seed_project(), _seed_scenarios_with_tasks(), _seed_simulation() (+3 more)

### Community 32 - "Submissions: Types Codeeditor.jsx"
Cohesion: 0.11
Nodes (28): CodeEditor(), inferLanguage(), LANG_MAP, templateFor(), criteriaLines(), criteriaStateLabel(), currentVersion(), earlierCriteria() (+20 more)

### Community 33 - "Submissions: Review Resubmission"
Cohesion: 0.11
Nodes (22): _ChangesReviewer, _CountingReviewer, _criteria_from_prompt(), _generate(), _headers_for(), _MarkerReviewer, _open_current_task(), _payload() (+14 more)

### Community 34 - "Assessment: Baseline Question"
Cohesion: 0.10
Nodes (23): baseline_difficulty(), calculate_baseline_results(), latest_result(), submit_attempt(), test_answer_scores_are_validated_and_missing_answers_are_not_scored(), test_competency_aggregation_uses_only_supported_question_mapping(), test_difficulty_uses_the_existing_baseline_thresholds(), test_question_total_is_an_unrounded_equal_weight_mean() (+15 more)

### Community 35 - "Assessment: Fallback Question"
Cohesion: 0.14
Nodes (24): generate_dynamic_baseline_questions(), _personalized_fallback(), _validated_technical_profile(), FakeAI, FakeSession, install_generation_fakes(), run_inline(), technical_profile() (+16 more)

### Community 36 - "Agent: Feature Features"
Cohesion: 0.06
Nodes (34): Agent coordination, Handoffs, Refresh and frontend, Reliability, Activity Log (Feature 13), API Reference, Bug reports (feeds Features 8 & 9), Chat (Features 6 & 7) (+26 more)

### Community 37 - "Simulation: Task .test"
Cohesion: 0.10
Nodes (11): auth_headers(), client(), _create_simulation_via_db(), _generate(), make_token(), _setup_database(), _start_and_get_current_task(), TestApiCannotBypassLocking (+3 more)

### Community 38 - "Simulation: Task Path"
Cohesion: 0.12
Nodes (11): _full_setup(), _generate(), _headers(), make_token(), _project_for_simulation(), _seed_company(), _seed_project(), _seed_scenario() (+3 more)

### Community 39 - "Simulation: Task Message"
Cohesion: 0.15
Nodes (24): _age_proactive_messages(), _backdate_task_deadline(), _backdate_task_started(), _deadline_notification_count(), _generate(), _manager_history(), _no_external_ai(), _proactive_count() (+16 more)

### Community 40 - "Simulation: Task Review"
Cohesion: 0.14
Nodes (16): _access_snapshot(), _ApprovingReviewer, _criteria_from_prompt(), _generate(), _headers_for(), _project_task_ids(), _review_version(), _StrictReviewer (+8 more)

### Community 41 - "Assessment: Downgrade Upgrade"
Cohesion: 0.07
Nodes (8): upgrade(), upgrade(), upgrade(), upgrade(), upgrade(), upgrade(), upgrade(), upgrade()

### Community 42 - "Security: Student Register"
Cohesion: 0.15
Nodes (13): login_route(), LoginRequest, me(), register(), RegisterRequest, hash_password(), verify_password(), create_access_token() (+5 more)

### Community 43 - "Simulation: Step Ambientbackground.jsx"
Cohesion: 0.17
Nodes (22): AmbientBackground(), LoadingScreen(), RequireSimulation(), useGenerateSimulation(), pickResumableSimulation(), toSimulationContext(), useSimulationList(), buildTechnicalProfilePayload() (+14 more)

### Community 44 - "Agent: Competency Evidence"
Cohesion: 0.19
Nodes (19): CompetencyAgent, CompetencyAgentInput, register_competency_agent(), case(), FakeContextManager, _immediate_wait_for(), no_platform_event_loop(), _ref() (+11 more)

### Community 45 - "Task: Criteria Version"
Cohesion: 0.17
Nodes (15): downgrade(), upgrade(), active_version(), backfill_missing_versions(), _clean_texts(), criteria_identity(), criteria_view(), _event_added_text() (+7 more)

### Community 46 - "Assessment: Question Admin"
Cohesion: 0.17
Nodes (15): admin_create_question(), admin_delete_question(), admin_questions(), admin_update_question(), AnswerItem, history(), latest(), QuestionWrite (+7 more)

### Community 47 - "Simulation: Requirement Change"
Cohesion: 0.22
Nodes (19): make_token(), test_backfill_preserves_the_task_array_and_links_the_event(), test_multiple_open_events_do_not_fail_simulation_reads(), _changes(), _raise_change(), _send_approach_note(), test_adaptation_that_mentions_the_new_requirement_is_scored(), test_communication_rubric_distinguishes_relevance_vagueness_and_detail() (+11 more)

### Community 48 - "Simulation: Scenario Status"
Cohesion: 0.12
Nodes (5): _create_test_data(), _get_scenario_status(), _get_task_status(), _set_task_status(), _setup_database()

### Community 49 - "Testing: Technical Domain Catalog / Test Technical Domain Catalog"
Cohesion: 0.12
Nodes (12): get_technical_domain(), list_technical_domains(), _slug(), TechnicalDomain, Technology, _technology(), test_catalog_exposes_exactly_five_approved_domains_with_stable_labels(), test_domain_lookup_returns_none_for_unapproved_identifiers() (+4 more)

### Community 50 - "Agent: Task Focus"
Cohesion: 0.12
Nodes (10): FakeAI, FakeDB, _immediate_wait_for(), no_platform_event_loop(), Result, run(), setup(), test_absent_or_untrusted_focus_preserves_existing_dynamic_task_choice() (+2 more)

### Community 51 - "Simulation: Manager Generate"
Cohesion: 0.14
Nodes (16): _generate(), test_company_and_manager_readable(), test_cross_tenant_access_is_forbidden(), test_generate_creates_full_simulation(), test_kanban_board_groups_all_tasks(), test_project_and_state_readable(), test_task_dependency_blocks_start_until_anchor_completed(), _approve_task() (+8 more)

### Community 53 - "Simulation: Path Task"
Cohesion: 0.15
Nodes (10): _build_explicit_path(), _build_personalised_path(), build_task_path(), _ensure_dependencies(), _add(), get_canonical_tasks_for_project(), get_current_path_entry(), _get_sprint_number() (+2 more)

### Community 54 - "Assessment: Service Dynamic"
Cohesion: 0.09
Nodes (10): test_contextual_adaptability_service_exists(), test_dynamic_baseline_service_exists(), test_dynamic_task_generator_exists(), test_performance_trend_service_exists(), test_progression_service_uses_sprint_objective(), test_review_pipeline_integration(), test_review_service_exists(), test_simulation_start_integration() (+2 more)

### Community 55 - "Assessment: Student Profile"
Cohesion: 0.13
Nodes (9): _admin_headers(), test_profile_skills_assessment_and_recommendation(), _headers(), test_assessed_profile_controls_generation_and_task_path(), test_websocket_accepts_the_owning_student(), test_websocket_receives_task_update_for_owning_student(), _criteria_from_prompt(), _EvidenceReviewer (+1 more)

### Community 56 - "Testing: Test Gemini Overload Retry"
Cohesion: 0.17
Nodes (11): _FakeClient, _ok_payload(), _patch_client(), _patch_sleep(), _resp(), _service(), test_503_then_success_returns_text_with_one_retry(), test_is_transient_ai_overload() (+3 more)

### Community 57 - "Submissions: Review Prompt"
Cohesion: 0.14
Nodes (13): is_json_response_cacheable(), build_compact_review_prompt(), build_review_prompt(), classify_provider_failure(), metadata_evidence_text(), test_long_architecture_spec_and_sql_comment_format_are_preserved_for_evaluation(), test_prompt_includes_submission_files_test_results_and_exact_criteria(), test_provider_failures_are_classified_without_fabricating_results() (+5 more)

### Community 58 - "Simulation: Simulation Test:"
Cohesion: 0.10
Nodes (5): _create_simulation_via_db(), _generate(), TestDatabaseRelationships, TestEventHistory, TestStartSimulation

### Community 59 - "Security: Feature Websocket"
Cohesion: 0.15
Nodes (4): list_activity(), company_ws(), decode_token(), get_current_user()

### Community 60 - "Simulation: .generate Gemini"
Cohesion: 0.15
Nodes (9): _assert_complete_simulation(), _DeadGemini, _OverloadedGemini, _payload(), _SlowGemini, test_generate_succeeds_when_gemini_returns_503(), test_generate_succeeds_when_gemini_unreachable(), test_generation_request_retry_returns_same_simulation() (+1 more)

### Community 61 - "Assessment: Client.js Apiclient"
Cohesion: 0.19
Nodes (15): apiClient, setAuthFailureHandler(), setAuthToken(), AuthContext, AuthProvider(), persist(), Admin(), AssessmentBank() (+7 more)

### Community 62 - "Task: Meta Status"
Cohesion: 0.20
Nodes (15): Column(), slugify(), StatusStrip(), TaskCard(), useBoard(), useUpdateTaskStatus(), moodColor(), moodLabel() (+7 more)

### Community 63 - "Agent: Context Contracts"
Cohesion: 0.19
Nodes (12): AgentContextManager, ContextAccessDenied, FakeSession, records(), test_agent_context_remains_compatible_with_orchestration_contracts(), test_context_construction_and_evidence_reference_integrity(), test_missing_simulation_or_task_is_handled_without_fabricated_context(), test_missing_soft_skill_evidence_is_not_encoded_as_a_low_score() (+4 more)

### Community 64 - "Deps / Companies Module"
Cohesion: 0.18
Nodes (8): get_owned_company(), get_company(), get_company_manager(), list_my_companies(), get_db(), Company, CompanyRead, ManagerRead

### Community 65 - "Testing: Test Review Provider Validation / Ai Service"
Cohesion: 0.18
Nodes (10): MalformedAIJSONError, parse_json_response(), _fake_provider(), _FakeGeminiResponse, _run_immediate(), test_malformed_json_is_explicitly_rejected(), test_malformed_json_is_not_repaired_or_retried(), test_other_gemini_features_keep_their_existing_two_request_limit() (+2 more)

### Community 66 - "Simulation: Simulation Task (66)"
Cohesion: 0.16
Nodes (7): can_start_simulation_task(), can_submit_simulation_task(), get_project_status_for_simulation_state(), get_simulation_task_transition_action(), is_terminal_simulation_task_state(), SimulationTaskState, TestStateMachineTransitions

### Community 67 - "Submissions: Calculate Next"
Cohesion: 0.14
Nodes (10): calculate_next_interval(), test_again(), test_easy(), test_good(), test_hard(), test_again(), test_easy(), test_good() (+2 more)

### Community 68 - "Agent: Criteria Event"
Cohesion: 0.24
Nodes (13): _criteria(), _criteria_id(), _dedupe_refs(), _evidence(), _interaction_evaluation(), _message(), _project_event(), _review() (+5 more)

### Community 69 - "Agent: Analytics Simulation"
Cohesion: 0.20
Nodes (11): analytics(), FakeContextManager, FakeDB, _immediate_wait_for(), no_platform_event_loop(), run(), setup(), test_non_owner_cannot_request_simulation_competency_assessment() (+3 more)

### Community 70 - "Misc / Meetings Module"
Cohesion: 0.20
Nodes (9): complete_meeting(), list_meetings(), _owned_project(), ActivityLogRead, EventRead, MeetingComplete, MeetingRead, NotificationRead (+1 more)

### Community 71 - "Assessment: Assessmenthistory.jsx Assessmentfeedback"
Cohesion: 0.33
Nodes (15): AssessmentFeedback(), AssessmentHistory(), CompetencyComparison(), formatDate(), formatTechnologyRatings(), HistoricalField(), scoreLabel(), ASSESSMENT_HISTORY_PAGE_SIZE (+7 more)

### Community 72 - "Submissions: Context Build"
Cohesion: 0.21
Nodes (7): build_project_context(), build_review_context(), build_task_context(), _criteria_identity(), _review_criteria(), test_successive_requirement_changes_version_the_criteria(), _versions()

### Community 73 - "Security: Settings Dev"
Cohesion: 0.15
Nodes (5): DevTokenRequest, DevTokenResponse, mint_dev_token(), get_settings(), Settings

### Community 74 - "Ai Service Module"
Cohesion: 0.15
Nodes (5): AIProviderResponseError, GeminiAIService, is_transient_ai_overload(), _log_provider_json_response(), _redacted_response_debug()

### Community 75 - "Submissions: Task Test:"
Cohesion: 0.12
Nodes (6): _start_and_get_current_task(), TestNextTaskAssignment, TestOnTimeSubmission, TestReworkKeepsTaskActive, TestSubmitTask, TestSubmitWithoutStarting

### Community 76 - "Submissions: Extract Content"
Cohesion: 0.21
Nodes (6): ContentExtractionError, extract_all_submission_files(), _extract_json(), _extract_plain_text(), extract_text_content(), _extract_yaml()

### Community 77 - "Simulation: Task Create"
Cohesion: 0.23
Nodes (5): _create_company(), _create_project(), _create_scenario_and_tasks(), _create_simulation(), _create_sprints_and_tasks()

### Community 78 - "Simulation: Create Student"
Cohesion: 0.18
Nodes (3): _create_company(), _create_simulation(), _seed_project_with_tasks()

### Community 79 - "Analytics: Route Activity"
Cohesion: 0.29
Nodes (8): activity_route(), analytics_route(), dashboard_route(), student_activity_route(), student_detail_route(), students_route(), CurrentUser, require_role()

### Community 80 - "Submissions: That Invalid"
Cohesion: 0.21
Nodes (6): make_token(), test_blocked_task_dependency(), test_complete_submission_workflow(), test_invalid_file_rejection(), test_invalid_state_transition(), test_unauthorized_submission()

### Community 81 - "Assessment: Fallback Questions"
Cohesion: 0.23
Nodes (10): _force_baseline_fallback(), _profile(), test_domain_specific_profiles_shape_fallback_questions(), test_low_self_rated_skill_changes_assessment_focus(), test_malformed_ai_uses_domain_aware_fallback(), test_retry_reuses_active_attempt_and_completed_attempt_can_restart(), test_same_domain_and_ratings_get_deterministic_fallback_questions(), test_valid_ai_questions_are_persisted_and_returned() (+2 more)

### Community 82 - "Simulation: Postgres Database"
Cohesion: 0.14
Nodes (14): 1. Create the database and user, 2. Set up the backend, 3. Configure environment, 4. Run migrations, 5. Start the server, (change POSTGRES_HOST=db to POSTGRES_HOST=localhost, or whatever your host is), edit .env — set DATABASE_URL to point at your Postgres instance, Environment variables (backend/.env) (+6 more)

### Community 83 - "Simulation: Task Dynamic"
Cohesion: 0.21
Nodes (5): TaskPriority, TaskType, _build_fallback_dynamic_task(), generate_dynamic_task(), test_dynamic_task_generation_and_adaptation()

### Community 85 - "Assessment: Workplacescenario.js Activerequirementchange"
Cohesion: 0.38
Nodes (10): activeRequirementChange(), FUTURE_SCENARIOS, observationLabel(), popupRequired(), restoredPhase(), pending, workplacePhase(), AssessmentSummary() (+2 more)

### Community 86 - "Assessment: Auth Headers"
Cohesion: 0.23
Nodes (7): auth_headers(), baseline_evaluator(), client(), db(), mock_student(), other_auth_headers(), _setup_database()

### Community 88 - "Simulation: .test Simulation"
Cohesion: 0.29
Nodes (6): client(), _headers(), make_token(), _seed(), _setup_database(), TestSimulationInteractionHistory

### Community 89 - "Simulation: Simulation Auth"
Cohesion: 0.24
Nodes (6): auth_headers(), client(), make_token(), other_auth_headers(), _setup_database(), TestSimulationProgress

### Community 90 - "Simulation: Check Fastapi"
Cohesion: 0.24
Nodes (3): health_check(), lifespan(), send_proactive_workplace_messages()

### Community 91 - "Testing: Events"
Cohesion: 0.24
Nodes (4): list_events(), manually_trigger_event(), _owned_project(), TriggerEventRequest

### Community 92 - "Projects / Project Module"
Cohesion: 0.31
Nodes (5): _get_owned_project(), get_project(), get_project_state(), list_my_projects(), ProjectStateRead

### Community 93 - "Simulation: Sprints.py Assert"
Cohesion: 0.40
Nodes (5): _assert_project_owned(), get_sprint(), list_sprints(), SprintRead, SprintWithTasks

### Community 94 - "Task: Provider Review"
Cohesion: 0.29
Nodes (8): validate_provider_review(), test_gemini_review_uses_structured_schema_and_returns_real_provider_evaluation(), test_json_parser_accepts_object_fences_and_harmless_surrounding_text(), test_malformed_or_inconsistent_provider_output_is_rejected(), test_provider_result_is_preserved_after_valid_schema_and_rubric_validation(), test_timeout_retries_once_then_accepts_success(), _valid_payload(), _valid_provider_review_text()

### Community 95 - "Submissions: Results Research"
Cohesion: 0.20
Nodes (10): Actual harness results, Architecture and responsibility boundaries, Experimental methodology, Limitations and threats to validity, Next steps before project submission, Proposed contribution, Research problem and motivation, Research validation: evidence-driven internship simulation (+2 more)

### Community 96 - "Simulation: Manager Profile"
Cohesion: 0.24
Nodes (3): _headers(), test_manager_review_does_not_call_gemini_again(), test_profile_generation_links_a_scenario_for_workflow_events()

### Community 97 - "Assessment: Snapshot Attempt"
Cohesion: 0.29
Nodes (6): _raise_generation_error(), _select_domain(), test_legacy_null_snapshot_attempt_remains_submittable(), test_new_attempt_snapshot_survives_question_edits_and_completes_atomically(), test_snapshot_failure_rolls_back_new_questions_and_attempt(), test_student_assessment_api_contract_never_returns_private_snapshot()

### Community 98 - "Notifications Module"
Cohesion: 0.36
Nodes (3): list_notifications(), mark_notification_read(), _owned_company()

### Community 100 - "Content Extraction Module"
Cohesion: 0.22
Nodes (4): _extract_zip(), is_text_file(), _is_unsafe_file(), _sanitize_zip_path()

### Community 101 - "Testing: Workplace Scenarios / Test Workplace Requirement Change"
Cohesion: 0.25
Nodes (6): _require_activity(), scenario_catalog(), ScenarioSpec, start_scenario(), WorkplaceScenarioError, test_future_scenarios_are_reserved_and_do_not_write()

### Community 102 - "Simulation: Database Workflow"
Cohesion: 0.22
Nodes (9): AI Workflow Simulation Engine — Database Setup, Application Configuration, Backup Files, Current Verified Data, Database Status, Database Tables, Database Version, Important (+1 more)

### Community 103 - "Assessment: Projection Result"
Cohesion: 0.39
Nodes (5): _assessment_result_projection(), test_legacy_projection_never_infers_current_profile_values(), test_missing_or_non_list_ratings_remain_unavailable(), test_rating_list_with_only_malformed_entries_projects_to_empty_list(), test_snapshot_projection_uses_historical_values_and_redacts_private_data()

### Community 104 - "Simulation: Transition Simulation"
Cohesion: 0.29
Nodes (3): InvalidSimulationTaskTransitionError, validate_simulation_task_transition(), TestInvalidStateTransition

### Community 106 - "Simulation: Local Included"
Cohesion: 0.25
Nodes (8): Adaptive demo, AI Internship Workflow Simulation — Integrated Project, Local auth, No API key is included, Option A — Docker for PostgreSQL only, Option B — Existing local PostgreSQL, Recommended local run, What is included

### Community 110 - "Simulation: Module Contract"
Cohesion: 0.29
Nodes (7): AI provider contract, Four-module integration contract, Module 1 — Student Management, Module 2 — Workflow Simulation, Module 3 — AI Evaluation, Module 4 — Feedback, readiness and administration, Shared identifiers

### Community 111 - "Simulation: Phase Verification"
Cohesion: 0.29
Nodes (7): Environment used, Phase 2 — Frontend verification, Phase 3 — AI chat, dynamic events, meetings, real-time verification, Verification Log, What was NOT run, What was NOT run for Phase 3, What was run

### Community 114 - "UI: .Oxlintrc"
Cohesion: 0.33
Nodes (5): plugins, rules, react/only-export-components, react/rules-of-hooks, $schema

### Community 116 - "Simulation: Integrating With"
Cohesion: 0.40
Nodes (5): Integrating with Module 1 (Auth / Login), Integrating with Module 3 (or any downstream consumer), Integration Guide, WebSockets (implemented), What's intentionally NOT shared

### Community 117 - "Simulation: Flow Admin"
Cohesion: 0.40
Nodes (5): Admin flow, Decline alert, Dynamic proof, Local test plan, Student flow

### Community 119 - "Criteria Version Service / Workplace Scenarios Module"
Cohesion: 0.50
Nodes (3): added_text_for_version(), _added_criterion(), _manager_message()

### Community 121 - "Simulation: Container Image"
Cohesion: 0.50
Nodes (4): Container image, Deployment Guide, Production checklist, Scaling

### Community 122 - "Simulation: Adaptive Intelligence"
Cohesion: 0.50
Nodes (4): Adaptive intelligence integration, AI provider boundary, Flow, Important data objects

### Community 123 - "Simulation: React Expanding"
Cohesion: 0.50
Nodes (4): Expanding the Oxlint configuration, Integrated intelligence UI, React Compiler, React + Vite

### Community 126 - "Simulation: Setup Apply"
Cohesion: 0.67
Nodes (3): Apply migrations and run tests, One-time administrator setup, Safe PostgreSQL test setup

## Knowledge Gaps
- **28 isolated node(s):** `AI Internship Workflow Simulation — Integrated Project`, `Adaptive demo`, `Safe PostgreSQL test setup`, `Apply migrations and run tests`, `AI Workflow Simulation Engine — Database Setup` (+23 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 1112 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **65 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Task` connect `Submissions: Workflow Task` to `Assessment: Adaptive Technical`, `Agent: Task Downgrade`, `Simulation: Task Sprint`, `Agent: Feature Session`, `Agent: Review Task`, `Simulation: Manager Message`, `Submissions: Submission Task`, `Submissions: Generate Review`, `Simulation: Simulation Task`, `Assessment: Requirement Change`, `Agent: Review Technical`, `Simulation: Task Verbs`, `Submissions: Review Resubmission`, `Simulation: Task Path`, `Simulation: Task Review`, `Task: Criteria Version`, `Simulation: Requirement Change`, `Simulation: Path Task`, `Agent: Context Contracts`, `Submissions: Context Build`, `Simulation: Task Dynamic`, `Submissions: Submit Simulation`, `Simulation: Manager Profile`, `Criteria Version Service / Workplace Scenarios Module`?**
  _High betweenness centrality (0.057) - this node is a cross-community bridge._
- **Are the 149 inferred relationships involving `Task` (e.g. with `AgentContextManager` and `_criteria_provenance()`) actually correct?**
  _`Task` has 149 INFERRED edges - model-reasoned connections that need verification._
- **What connects `AI Internship Workflow Simulation — Integrated Project`, `Adaptive demo`, `Safe PostgreSQL test setup` to the rest of the system?**
  _28 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Assessment: Adaptive Technical` be split into smaller, more focused modules?**
  _Cohesion score 0.05625366999412801 - nodes in this community are weakly interconnected._
- **Why does `Simulation` connect `Submissions: Workflow Task` to `Assessment: Adaptive Technical`, `Agent: Task Downgrade`, `Simulation: Task Sprint`, `Agent: Feature Session`, `Agent: Review Task`, `Simulation: Manager Message`, `Simulation: Simulation Task`, `Assessment: Requirement Change`, `Agent: Review Technical`, `Simulation: Task Path`, `Simulation: Task Review`, `Simulation: Requirement Change`, `Simulation: Path Task`, `Assessment: Student Profile`, `Agent: Context Contracts`, `Agent: Analytics Simulation`, `Submissions: Context Build`, `Simulation: Task Dynamic`, `Submissions: Submit Simulation`, `Simulation: Manager Profile`, `Criteria Version Service / Workplace Scenarios Module`?**
  _High betweenness centrality (0.044) - this node is a cross-community bridge._
- **Are the 128 inferred relationships involving `Simulation` (e.g. with `AgentContextManager` and `TechnicalReviewAgent`) actually correct?**
  _`Simulation` has 128 INFERRED edges - model-reasoned connections that need verification._
- **Should `Agent: Task Downgrade` be split into smaller, more focused modules?**
  _Cohesion score 0.06526610644257703 - nodes in this community are weakly interconnected._