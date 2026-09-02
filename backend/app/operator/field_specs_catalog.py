from __future__ import annotations

# Explicit production FieldSpec source. Compatibility field-name tuples in registry.py
# are checked against this catalog but never generate semantic contracts.
FIELD_SPEC_CATALOG = {'job': {'id': {'name': 'id',
                'data_type': 'integer',
                'description': 'Stable database record identifier exposed read-only in the public record envelope.',
                'semantic_role': 'job_id',
                'data_origin': 'backend',
                'write_owner': 'backend',
                'readable': True,
                'generic_creatable': False,
                'generic_writable': False,
                'filterable': True,
                'searchable': False,
                'summary_visible': True,
                'detail_visible': True,
                'long_text': False,
                'required_on_create': False,
                'nullable': False,
                'enum_values': (),
                'relation_target': None,
                'aliases': (),
                'examples': (),
                'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                'forbidden_uses': (),
                'internal': False},
         'title': {'name': 'title',
                   'data_type': 'string',
                   'description': 'Human-readable title used to identify this business record.',
                   'semantic_role': 'job_title',
                   'data_origin': 'user_or_system',
                   'write_owner': 'user_or_agent',
                   'readable': True,
                   'generic_creatable': True,
                   'generic_writable': False,
                   'filterable': True,
                   'searchable': True,
                   'summary_visible': True,
                   'detail_visible': True,
                   'long_text': False,
                   'required_on_create': True,
                   'nullable': False,
                   'enum_values': (),
                   'relation_target': None,
                   'aliases': (),
                   'examples': (),
                   'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                   'forbidden_uses': (),
                   'internal': False},
         'company': {'name': 'company',
                     'data_type': 'string',
                     'description': 'Organization associated with this business record.',
                     'semantic_role': 'job_company',
                     'data_origin': 'user_or_system',
                     'write_owner': 'user_or_agent',
                     'readable': True,
                     'generic_creatable': True,
                     'generic_writable': False,
                     'filterable': True,
                     'searchable': True,
                     'summary_visible': True,
                     'detail_visible': True,
                     'long_text': False,
                     'required_on_create': True,
                     'nullable': False,
                     'enum_values': (),
                     'relation_target': None,
                     'aliases': (),
                     'examples': (),
                     'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                     'forbidden_uses': (),
                     'internal': False},
         'location': {'name': 'location',
                      'data_type': 'string',
                      'description': 'Human-readable geographic or remote-work location.',
                      'semantic_role': 'job_location',
                      'data_origin': 'user_or_system',
                      'write_owner': 'user_or_agent',
                      'readable': True,
                      'generic_creatable': True,
                      'generic_writable': False,
                      'filterable': True,
                      'searchable': True,
                      'summary_visible': True,
                      'detail_visible': True,
                      'long_text': False,
                      'required_on_create': False,
                      'nullable': False,
                      'enum_values': (),
                      'relation_target': None,
                      'aliases': (),
                      'examples': (),
                      'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                      'forbidden_uses': (),
                      'internal': False},
         'url': {'name': 'url',
                 'data_type': 'string',
                 'description': 'Canonical public listing URL used to revisit the source and detect duplicate '
                                'captures.',
                 'semantic_role': 'job_url',
                 'data_origin': 'user_or_system',
                 'write_owner': 'user_or_agent',
                 'readable': True,
                 'generic_creatable': True,
                 'generic_writable': False,
                 'filterable': False,
                 'searchable': False,
                 'summary_visible': False,
                 'detail_visible': True,
                 'long_text': False,
                 'required_on_create': False,
                 'nullable': False,
                 'enum_values': (),
                 'relation_target': None,
                 'aliases': (),
                 'examples': (),
                 'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                 'forbidden_uses': (),
                 'internal': False},
         'apply_url': {'name': 'apply_url',
                       'data_type': 'string',
                       'description': 'Direct employer or recruiting-platform endpoint used to submit or revisit an '
                                      'application.',
                       'semantic_role': 'job_apply_url',
                       'data_origin': 'user_or_system',
                       'write_owner': 'user_or_agent',
                       'readable': True,
                       'generic_creatable': True,
                       'generic_writable': False,
                       'filterable': False,
                       'searchable': False,
                       'summary_visible': False,
                       'detail_visible': True,
                       'long_text': False,
                       'required_on_create': False,
                       'nullable': False,
                       'enum_values': (),
                       'relation_target': None,
                       'aliases': (),
                       'examples': (),
                       'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                       'forbidden_uses': (),
                       'internal': False},
         'source': {'name': 'source',
                    'data_type': 'string',
                    'description': 'Origin channel or provider retained to preserve provenance for the record.',
                    'semantic_role': 'job_source',
                    'data_origin': 'user_or_system',
                    'write_owner': 'user_or_agent',
                    'readable': True,
                    'generic_creatable': True,
                    'generic_writable': False,
                    'filterable': True,
                    'searchable': False,
                    'summary_visible': True,
                    'detail_visible': True,
                    'long_text': False,
                    'required_on_create': False,
                    'nullable': False,
                    'enum_values': (),
                    'relation_target': None,
                    'aliases': (),
                    'examples': (),
                    'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                    'forbidden_uses': (),
                    'internal': False},
         'raw_description': {'name': 'raw_description',
                             'data_type': 'string',
                             'description': 'Source or user-provided job description used as the authoritative role '
                                            'content.',
                             'semantic_role': 'source_job_description',
                             'data_origin': 'source_or_user',
                             'write_owner': 'source_or_user',
                             'readable': True,
                             'generic_creatable': True,
                             'generic_writable': False,
                             'filterable': False,
                             'searchable': True,
                             'summary_visible': False,
                             'detail_visible': True,
                             'long_text': True,
                             'required_on_create': False,
                             'nullable': False,
                             'enum_values': (),
                             'relation_target': None,
                             'aliases': (),
                             'examples': (),
                             'write_guidance': 'Use only for the registered semantic role and preserve model '
                                               'validation.',
                             'forbidden_uses': ('Do not use this field for screening notes or AI analysis summaries.',),
                             'internal': False},
         'posted_at': {'name': 'posted_at',
                       'data_type': 'datetime',
                       'description': 'Publication timestamp reported by the source, used to judge listing freshness.',
                       'semantic_role': 'job_posted_at',
                       'data_origin': 'user_or_system',
                       'write_owner': 'user_or_agent',
                       'readable': True,
                       'generic_creatable': True,
                       'generic_writable': False,
                       'filterable': False,
                       'searchable': False,
                       'summary_visible': False,
                       'detail_visible': True,
                       'long_text': False,
                       'required_on_create': False,
                       'nullable': True,
                       'enum_values': (),
                       'relation_target': None,
                       'aliases': (),
                       'examples': (),
                       'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                       'forbidden_uses': (),
                       'internal': False},
         'salary_min': {'name': 'salary_min',
                        'data_type': 'integer',
                        'description': 'Normalized lower compensation bound used for numeric comparison across '
                                       'opportunities.',
                        'semantic_role': 'job_salary_min',
                        'data_origin': 'user_or_system',
                        'write_owner': 'user_or_agent',
                        'readable': True,
                        'generic_creatable': True,
                        'generic_writable': False,
                        'filterable': False,
                        'searchable': False,
                        'summary_visible': False,
                        'detail_visible': False,
                        'long_text': False,
                        'required_on_create': False,
                        'nullable': True,
                        'enum_values': (),
                        'relation_target': None,
                        'aliases': (),
                        'examples': (),
                        'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                        'forbidden_uses': (),
                        'internal': False},
         'salary_max': {'name': 'salary_max',
                        'data_type': 'integer',
                        'description': 'Normalized upper compensation bound used for numeric comparison across '
                                       'opportunities.',
                        'semantic_role': 'job_salary_max',
                        'data_origin': 'user_or_system',
                        'write_owner': 'user_or_agent',
                        'readable': True,
                        'generic_creatable': True,
                        'generic_writable': False,
                        'filterable': False,
                        'searchable': False,
                        'summary_visible': False,
                        'detail_visible': False,
                        'long_text': False,
                        'required_on_create': False,
                        'nullable': True,
                        'enum_values': (),
                        'relation_target': None,
                        'aliases': (),
                        'examples': (),
                        'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                        'forbidden_uses': (),
                        'internal': False},
         'salary_text': {'name': 'salary_text',
                         'data_type': 'string',
                         'description': 'Human-readable compensation text preserved when numeric normalization is '
                                        'incomplete.',
                         'semantic_role': 'job_salary_text',
                         'data_origin': 'user_or_system',
                         'write_owner': 'user_or_agent',
                         'readable': True,
                         'generic_creatable': True,
                         'generic_writable': False,
                         'filterable': False,
                         'searchable': False,
                         'summary_visible': False,
                         'detail_visible': True,
                         'long_text': False,
                         'required_on_create': False,
                         'nullable': False,
                         'enum_values': (),
                         'relation_target': None,
                         'aliases': (),
                         'examples': (),
                         'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                         'forbidden_uses': (),
                         'internal': False},
         'education': {'name': 'education',
                       'data_type': 'string',
                       'description': 'Education or credential requirement stated by an employer for the role.',
                       'semantic_role': 'job_education',
                       'data_origin': 'user_or_system',
                       'write_owner': 'user_or_agent',
                       'readable': True,
                       'generic_creatable': True,
                       'generic_writable': False,
                       'filterable': False,
                       'searchable': False,
                       'summary_visible': False,
                       'detail_visible': True,
                       'long_text': False,
                       'required_on_create': False,
                       'nullable': False,
                       'enum_values': (),
                       'relation_target': None,
                       'aliases': (),
                       'examples': (),
                       'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                       'forbidden_uses': (),
                       'internal': False},
         'experience': {'name': 'experience',
                        'data_type': 'string',
                        'description': 'Experience level or tenure requirement stated by an employer for the role.',
                        'semantic_role': 'job_experience',
                        'data_origin': 'user_or_system',
                        'write_owner': 'user_or_agent',
                        'readable': True,
                        'generic_creatable': True,
                        'generic_writable': False,
                        'filterable': False,
                        'searchable': False,
                        'summary_visible': False,
                        'detail_visible': True,
                        'long_text': False,
                        'required_on_create': False,
                        'nullable': False,
                        'enum_values': (),
                        'relation_target': None,
                        'aliases': (),
                        'examples': (),
                        'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                        'forbidden_uses': (),
                        'internal': False},
         'job_type': {'name': 'job_type',
                      'data_type': 'string',
                      'description': 'Employment arrangement such as full-time, internship, contract, or part-time.',
                      'semantic_role': 'job_job_type',
                      'data_origin': 'user_or_system',
                      'write_owner': 'user_or_agent',
                      'readable': True,
                      'generic_creatable': True,
                      'generic_writable': False,
                      'filterable': False,
                      'searchable': False,
                      'summary_visible': False,
                      'detail_visible': True,
                      'long_text': False,
                      'required_on_create': False,
                      'nullable': False,
                      'enum_values': (),
                      'relation_target': None,
                      'aliases': (),
                      'examples': (),
                      'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                      'forbidden_uses': (),
                      'internal': False},
         'company_size': {'name': 'company_size',
                          'data_type': 'string',
                          'description': 'Employer headcount or size band used to compare organizational context '
                                         'across opportunities.',
                          'semantic_role': 'job_company_size',
                          'data_origin': 'user_or_system',
                          'write_owner': 'user_or_agent',
                          'readable': True,
                          'generic_creatable': True,
                          'generic_writable': False,
                          'filterable': False,
                          'searchable': False,
                          'summary_visible': False,
                          'detail_visible': True,
                          'long_text': False,
                          'required_on_create': False,
                          'nullable': False,
                          'enum_values': (),
                          'relation_target': None,
                          'aliases': (),
                          'examples': (),
                          'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                          'forbidden_uses': (),
                          'internal': False},
         'company_industry': {'name': 'company_industry',
                              'data_type': 'string',
                              'description': 'Industry classification used for opportunity discovery, filtering, and '
                                             'fit analysis.',
                              'semantic_role': 'job_company_industry',
                              'data_origin': 'user_or_system',
                              'write_owner': 'user_or_agent',
                              'readable': True,
                              'generic_creatable': True,
                              'generic_writable': False,
                              'filterable': False,
                              'searchable': False,
                              'summary_visible': False,
                              'detail_visible': True,
                              'long_text': False,
                              'required_on_create': False,
                              'nullable': False,
                              'enum_values': (),
                              'relation_target': None,
                              'aliases': (),
                              'examples': (),
                              'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                'validation.',
                              'forbidden_uses': (),
                              'internal': False},
         'company_logo': {'name': 'company_logo',
                          'data_type': 'string',
                          'description': 'Display asset location for the employer logo in job discovery and comparison '
                                         'interfaces.',
                          'semantic_role': 'job_company_logo',
                          'data_origin': 'user_or_system',
                          'write_owner': 'user_or_agent',
                          'readable': True,
                          'generic_creatable': True,
                          'generic_writable': False,
                          'filterable': False,
                          'searchable': False,
                          'summary_visible': False,
                          'detail_visible': False,
                          'long_text': False,
                          'required_on_create': False,
                          'nullable': False,
                          'enum_values': (),
                          'relation_target': None,
                          'aliases': (),
                          'examples': (),
                          'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                          'forbidden_uses': (),
                          'internal': False},
         'is_campus': {'name': 'is_campus',
                       'data_type': 'boolean',
                       'description': 'Distinguishes campus or graduate recruiting from experienced-hire '
                                      'opportunities.',
                       'semantic_role': 'job_is_campus',
                       'data_origin': 'user_or_system',
                       'write_owner': 'user_or_agent',
                       'readable': True,
                       'generic_creatable': True,
                       'generic_writable': False,
                       'filterable': True,
                       'searchable': False,
                       'summary_visible': False,
                       'detail_visible': False,
                       'long_text': False,
                       'required_on_create': False,
                       'nullable': False,
                       'enum_values': (),
                       'relation_target': None,
                       'aliases': (),
                       'examples': (),
                       'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                       'forbidden_uses': (),
                       'internal': False},
         'summary': {'name': 'summary',
                     'data_type': 'string',
                     'description': 'AI-derived analysis summary of the job posting; it is not a user screening note.',
                     'semantic_role': 'ai_job_analysis_summary',
                     'data_origin': 'ai_analysis',
                     'write_owner': 'analysis_action',
                     'readable': True,
                     'generic_creatable': False,
                     'generic_writable': False,
                     'filterable': False,
                     'searchable': True,
                     'summary_visible': False,
                     'detail_visible': True,
                     'long_text': True,
                     'required_on_create': False,
                     'nullable': False,
                     'enum_values': (),
                     'relation_target': None,
                     'aliases': (),
                     'examples': (),
                     'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                     'forbidden_uses': ('Do not store user annotations or source job descriptions here.',),
                     'internal': False},
         'keywords': {'name': 'keywords',
                      'data_type': 'array',
                      'description': 'AI-derived analysis keywords for job matching and search; they are not user '
                                     'annotations.',
                      'semantic_role': 'ai_job_analysis_keywords',
                      'data_origin': 'ai_analysis',
                      'write_owner': 'analysis_action',
                      'readable': True,
                      'generic_creatable': False,
                      'generic_writable': False,
                      'filterable': True,
                      'searchable': True,
                      'summary_visible': False,
                      'detail_visible': True,
                      'long_text': False,
                      'required_on_create': False,
                      'nullable': True,
                      'enum_values': (),
                      'relation_target': None,
                      'aliases': (),
                      'examples': (),
                      'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                      'forbidden_uses': ('Do not store free-form user screening notes here.',),
                      'internal': False},
         'user_notes': {'name': 'user_notes',
                        'data_type': 'string',
                        'description': 'User-authored annotation recorded while discovering, comparing, or screening a '
                                       'job.',
                        'semantic_role': 'job_screening_annotation',
                        'data_origin': 'user',
                        'write_owner': 'user_or_agent',
                        'readable': True,
                        'generic_creatable': True,
                        'generic_writable': True,
                        'filterable': False,
                        'searchable': True,
                        'summary_visible': False,
                        'detail_visible': True,
                        'long_text': True,
                        'required_on_create': False,
                        'nullable': False,
                        'enum_values': (),
                        'relation_target': None,
                        'aliases': ('note', 'notes', '备注', '筛选备注'),
                        'examples': ('适合 agent 产品和数据分析方向',),
                        'write_guidance': 'Store user screening annotations here and merge them with compatible '
                                          'same-record status updates.',
                        'forbidden_uses': ('Do not treat this as source job content or an AI-derived analysis field.',),
                        'internal': False},
         'triage_status': {'name': 'triage_status',
                           'data_type': 'string',
                           'description': 'Canonical workflow state for the job inbox: inbox, picked, or ignored.',
                           'semantic_role': 'job_workflow_status',
                           'data_origin': 'user_or_workflow',
                           'write_owner': 'user_or_agent',
                           'readable': True,
                           'generic_creatable': True,
                           'generic_writable': True,
                           'filterable': True,
                           'searchable': False,
                           'summary_visible': True,
                           'detail_visible': True,
                           'long_text': False,
                           'required_on_create': False,
                           'nullable': False,
                           'enum_values': ('inbox', 'picked', 'ignored'),
                           'relation_target': None,
                           'aliases': (),
                           'examples': (),
                           'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                           'forbidden_uses': ('Do not encode notes, application status, or arbitrary labels in this '
                                              'field.',),
                           'internal': False},
         'pool_id': {'name': 'pool_id',
                     'data_type': 'integer',
                     'description': 'Actor-owned job pool containing the opportunity for user-defined organization and '
                                    'triage.',
                     'semantic_role': 'job_pool_id',
                     'data_origin': 'user_or_system',
                     'write_owner': 'user_or_agent',
                     'readable': True,
                     'generic_creatable': True,
                     'generic_writable': True,
                     'filterable': True,
                     'searchable': False,
                     'summary_visible': True,
                     'detail_visible': True,
                     'long_text': False,
                     'required_on_create': False,
                     'nullable': True,
                     'enum_values': (),
                     'relation_target': 'pool',
                     'aliases': (),
                     'examples': (),
                     'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                     'forbidden_uses': (),
                     'internal': False},
         'batch_id': {'name': 'batch_id',
                      'data_type': 'string',
                      'description': 'Acquisition-run lineage identifying which import or scraping batch produced the '
                                     'record.',
                      'semantic_role': 'job_batch_id',
                      'data_origin': 'user_or_system',
                      'write_owner': 'user_or_agent',
                      'readable': True,
                      'generic_creatable': True,
                      'generic_writable': False,
                      'filterable': True,
                      'searchable': False,
                      'summary_visible': False,
                      'detail_visible': True,
                      'long_text': False,
                      'required_on_create': False,
                      'nullable': False,
                      'enum_values': (),
                      'relation_target': 'batch',
                      'aliases': (),
                      'examples': (),
                      'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                      'forbidden_uses': (),
                      'internal': False},
         'created_at': {'name': 'created_at',
                        'data_type': 'datetime',
                        'description': 'Backend-generated creation timestamp exposed read-only in the record envelope.',
                        'semantic_role': 'job_created_at',
                        'data_origin': 'backend',
                        'write_owner': 'backend',
                        'readable': True,
                        'generic_creatable': False,
                        'generic_writable': False,
                        'filterable': False,
                        'searchable': False,
                        'summary_visible': False,
                        'detail_visible': True,
                        'long_text': False,
                        'required_on_create': False,
                        'nullable': False,
                        'enum_values': (),
                        'relation_target': None,
                        'aliases': (),
                        'examples': (),
                        'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                        'forbidden_uses': (),
                        'internal': False},
         'operator_version_hash': {'name': 'operator_version_hash',
                                   'data_type': 'string',
                                   'description': 'Backend-owned optimistic-concurrency token exposed read-only for '
                                                  'version-fenced operations.',
                                   'semantic_role': 'job_operator_version_hash',
                                   'data_origin': 'backend',
                                   'write_owner': 'backend',
                                   'readable': True,
                                   'generic_creatable': False,
                                   'generic_writable': False,
                                   'filterable': False,
                                   'searchable': False,
                                   'summary_visible': False,
                                   'detail_visible': True,
                                   'long_text': False,
                                   'required_on_create': False,
                                   'nullable': False,
                                   'enum_values': (),
                                   'relation_target': None,
                                   'aliases': (),
                                   'examples': (),
                                   'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                     'validation.',
                                   'forbidden_uses': (),
                                   'internal': False}},
 'batch': {'id': {'name': 'id',
                  'data_type': 'string',
                  'description': 'Stable database record identifier exposed read-only in the public record envelope.',
                  'semantic_role': 'batch_id',
                  'data_origin': 'backend',
                  'write_owner': 'backend',
                  'readable': True,
                  'generic_creatable': False,
                  'generic_writable': False,
                  'filterable': True,
                  'searchable': False,
                  'summary_visible': True,
                  'detail_visible': True,
                  'long_text': False,
                  'required_on_create': False,
                  'nullable': False,
                  'enum_values': (),
                  'relation_target': None,
                  'aliases': (),
                  'examples': (),
                  'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                  'forbidden_uses': (),
                  'internal': False},
           'source': {'name': 'source',
                      'data_type': 'string',
                      'description': 'Origin channel or provider retained to preserve provenance for the record.',
                      'semantic_role': 'batch_source',
                      'data_origin': 'user_or_system',
                      'write_owner': 'user_or_agent',
                      'readable': True,
                      'generic_creatable': False,
                      'generic_writable': False,
                      'filterable': True,
                      'searchable': True,
                      'summary_visible': True,
                      'detail_visible': True,
                      'long_text': False,
                      'required_on_create': False,
                      'nullable': False,
                      'enum_values': (),
                      'relation_target': None,
                      'aliases': (),
                      'examples': (),
                      'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                      'forbidden_uses': (),
                      'internal': False},
           'keywords': {'name': 'keywords',
                        'data_type': 'array',
                        'description': 'Search terms retained to explain which opportunities an acquisition run '
                                       'collected.',
                        'semantic_role': 'batch_keywords',
                        'data_origin': 'user_or_system',
                        'write_owner': 'user_or_agent',
                        'readable': True,
                        'generic_creatable': False,
                        'generic_writable': False,
                        'filterable': False,
                        'searchable': True,
                        'summary_visible': False,
                        'detail_visible': True,
                        'long_text': False,
                        'required_on_create': False,
                        'nullable': True,
                        'enum_values': (),
                        'relation_target': None,
                        'aliases': (),
                        'examples': (),
                        'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                        'forbidden_uses': (),
                        'internal': False},
           'location': {'name': 'location',
                        'data_type': 'string',
                        'description': 'Human-readable geographic or remote-work location.',
                        'semantic_role': 'batch_location',
                        'data_origin': 'user_or_system',
                        'write_owner': 'user_or_agent',
                        'readable': True,
                        'generic_creatable': False,
                        'generic_writable': False,
                        'filterable': True,
                        'searchable': True,
                        'summary_visible': True,
                        'detail_visible': True,
                        'long_text': False,
                        'required_on_create': False,
                        'nullable': False,
                        'enum_values': (),
                        'relation_target': None,
                        'aliases': (),
                        'examples': (),
                        'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                        'forbidden_uses': (),
                        'internal': False},
           'max_results': {'name': 'max_results',
                           'data_type': 'integer',
                           'description': 'Requested upper bound on source results for an acquisition run.',
                           'semantic_role': 'batch_max_results',
                           'data_origin': 'user_or_system',
                           'write_owner': 'user_or_agent',
                           'readable': True,
                           'generic_creatable': False,
                           'generic_writable': False,
                           'filterable': False,
                           'searchable': False,
                           'summary_visible': False,
                           'detail_visible': True,
                           'long_text': False,
                           'required_on_create': False,
                           'nullable': False,
                           'enum_values': (),
                           'relation_target': None,
                           'aliases': (),
                           'examples': (),
                           'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                           'forbidden_uses': (),
                           'internal': False},
           'job_count': {'name': 'job_count',
                         'data_type': 'integer',
                         'description': 'Number of durable Job records associated with the acquisition batch after '
                                        'deduplication.',
                         'semantic_role': 'batch_job_count',
                         'data_origin': 'user_or_system',
                         'write_owner': 'user_or_agent',
                         'readable': True,
                         'generic_creatable': False,
                         'generic_writable': False,
                         'filterable': False,
                         'searchable': False,
                         'summary_visible': True,
                         'detail_visible': True,
                         'long_text': False,
                         'required_on_create': False,
                         'nullable': False,
                         'enum_values': (),
                         'relation_target': None,
                         'aliases': (),
                         'examples': (),
                         'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                         'forbidden_uses': (),
                         'internal': False},
           'status': {'name': 'status',
                      'data_type': 'string',
                      'description': 'Registered lifecycle or workflow status for this business record.',
                      'semantic_role': 'batch_status',
                      'data_origin': 'user_or_system',
                      'write_owner': 'user_or_agent',
                      'readable': True,
                      'generic_creatable': False,
                      'generic_writable': False,
                      'filterable': True,
                      'searchable': False,
                      'summary_visible': True,
                      'detail_visible': True,
                      'long_text': False,
                      'required_on_create': False,
                      'nullable': False,
                      'enum_values': (),
                      'relation_target': None,
                      'aliases': (),
                      'examples': (),
                      'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                      'forbidden_uses': (),
                      'internal': False},
           'total_fetched': {'name': 'total_fetched',
                             'data_type': 'integer',
                             'description': 'Number of candidate listings returned upstream before durable import '
                                            'filtering.',
                             'semantic_role': 'batch_total_fetched',
                             'data_origin': 'user_or_system',
                             'write_owner': 'user_or_agent',
                             'readable': True,
                             'generic_creatable': False,
                             'generic_writable': False,
                             'filterable': False,
                             'searchable': False,
                             'summary_visible': False,
                             'detail_visible': True,
                             'long_text': False,
                             'required_on_create': False,
                             'nullable': False,
                             'enum_values': (),
                             'relation_target': None,
                             'aliases': (),
                             'examples': (),
                             'write_guidance': 'Use only for the registered semantic role and preserve model '
                                               'validation.',
                             'forbidden_uses': (),
                             'internal': False},
           'created_at': {'name': 'created_at',
                          'data_type': 'datetime',
                          'description': 'Backend-generated creation timestamp exposed read-only in the record '
                                         'envelope.',
                          'semantic_role': 'batch_created_at',
                          'data_origin': 'backend',
                          'write_owner': 'backend',
                          'readable': True,
                          'generic_creatable': False,
                          'generic_writable': False,
                          'filterable': True,
                          'searchable': False,
                          'summary_visible': False,
                          'detail_visible': True,
                          'long_text': False,
                          'required_on_create': False,
                          'nullable': False,
                          'enum_values': (),
                          'relation_target': None,
                          'aliases': (),
                          'examples': (),
                          'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                          'forbidden_uses': (),
                          'internal': False}},
 'pool': {'id': {'name': 'id',
                 'data_type': 'integer',
                 'description': 'Stable database record identifier exposed read-only in the public record envelope.',
                 'semantic_role': 'pool_id',
                 'data_origin': 'backend',
                 'write_owner': 'backend',
                 'readable': True,
                 'generic_creatable': False,
                 'generic_writable': False,
                 'filterable': True,
                 'searchable': False,
                 'summary_visible': True,
                 'detail_visible': True,
                 'long_text': False,
                 'required_on_create': False,
                 'nullable': False,
                 'enum_values': (),
                 'relation_target': None,
                 'aliases': (),
                 'examples': (),
                 'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                 'forbidden_uses': (),
                 'internal': False},
          'name': {'name': 'name',
                   'data_type': 'string',
                   'description': 'Human-readable name used to identify this business record.',
                   'semantic_role': 'pool_name',
                   'data_origin': 'user_or_system',
                   'write_owner': 'user_or_agent',
                   'readable': True,
                   'generic_creatable': True,
                   'generic_writable': True,
                   'filterable': True,
                   'searchable': True,
                   'summary_visible': True,
                   'detail_visible': True,
                   'long_text': False,
                   'required_on_create': True,
                   'nullable': False,
                   'enum_values': (),
                   'relation_target': None,
                   'aliases': (),
                   'examples': (),
                   'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                   'forbidden_uses': (),
                   'internal': False},
          'description': {'name': 'description',
                          'data_type': 'string',
                          'description': 'Business description for this record, interpreted according to the '
                                         'containing model contract.',
                          'semantic_role': 'pool_description',
                          'data_origin': 'user_or_system',
                          'write_owner': 'user_or_agent',
                          'readable': True,
                          'generic_creatable': True,
                          'generic_writable': True,
                          'filterable': False,
                          'searchable': True,
                          'summary_visible': False,
                          'detail_visible': True,
                          'long_text': True,
                          'required_on_create': False,
                          'nullable': False,
                          'enum_values': (),
                          'relation_target': None,
                          'aliases': (),
                          'examples': (),
                          'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                          'forbidden_uses': (),
                          'internal': False},
          'color': {'name': 'color',
                    'data_type': 'string',
                    'description': 'User-selected visual token used to distinguish this collection in triage views.',
                    'semantic_role': 'pool_color',
                    'data_origin': 'user_or_system',
                    'write_owner': 'user_or_agent',
                    'readable': True,
                    'generic_creatable': True,
                    'generic_writable': True,
                    'filterable': False,
                    'searchable': False,
                    'summary_visible': True,
                    'detail_visible': True,
                    'long_text': False,
                    'required_on_create': False,
                    'nullable': False,
                    'enum_values': (),
                    'relation_target': None,
                    'aliases': (),
                    'examples': (),
                    'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                    'forbidden_uses': (),
                    'internal': False},
          'sort_order': {'name': 'sort_order',
                         'data_type': 'integer',
                         'description': 'Stable user-facing ordering position among peer collections or content '
                                        'sections.',
                         'semantic_role': 'pool_sort_order',
                         'data_origin': 'user_or_system',
                         'write_owner': 'user_or_agent',
                         'readable': True,
                         'generic_creatable': True,
                         'generic_writable': True,
                         'filterable': False,
                         'searchable': False,
                         'summary_visible': True,
                         'detail_visible': True,
                         'long_text': False,
                         'required_on_create': False,
                         'nullable': False,
                         'enum_values': (),
                         'relation_target': None,
                         'aliases': (),
                         'examples': (),
                         'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                         'forbidden_uses': (),
                         'internal': False},
          'scope': {'name': 'scope',
                    'data_type': 'string',
                    'description': 'Visibility and ownership category controlling where a collection is shown and who '
                                   'may mutate it.',
                    'semantic_role': 'pool_scope',
                    'data_origin': 'user_or_system',
                    'write_owner': 'user_or_agent',
                    'readable': True,
                    'generic_creatable': True,
                    'generic_writable': True,
                    'filterable': True,
                    'searchable': False,
                    'summary_visible': True,
                    'detail_visible': True,
                    'long_text': False,
                    'required_on_create': False,
                    'nullable': False,
                    'enum_values': ('inbox', 'picked', 'ignored'),
                    'relation_target': None,
                    'aliases': (),
                    'examples': (),
                    'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                    'forbidden_uses': (),
                    'internal': False},
          'created_at': {'name': 'created_at',
                         'data_type': 'datetime',
                         'description': 'Backend-generated creation timestamp exposed read-only in the record '
                                        'envelope.',
                         'semantic_role': 'pool_created_at',
                         'data_origin': 'backend',
                         'write_owner': 'backend',
                         'readable': True,
                         'generic_creatable': False,
                         'generic_writable': False,
                         'filterable': False,
                         'searchable': False,
                         'summary_visible': False,
                         'detail_visible': True,
                         'long_text': False,
                         'required_on_create': False,
                         'nullable': False,
                         'enum_values': (),
                         'relation_target': None,
                         'aliases': (),
                         'examples': (),
                         'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                         'forbidden_uses': (),
                         'internal': False},
          'updated_at': {'name': 'updated_at',
                         'data_type': 'datetime',
                         'description': 'Backend-generated last-update timestamp exposed read-only in the record '
                                        'envelope.',
                         'semantic_role': 'pool_updated_at',
                         'data_origin': 'backend',
                         'write_owner': 'backend',
                         'readable': True,
                         'generic_creatable': False,
                         'generic_writable': False,
                         'filterable': False,
                         'searchable': False,
                         'summary_visible': False,
                         'detail_visible': True,
                         'long_text': False,
                         'required_on_create': False,
                         'nullable': False,
                         'enum_values': (),
                         'relation_target': None,
                         'aliases': (),
                         'examples': (),
                         'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                         'forbidden_uses': (),
                         'internal': False},
          'operator_version_hash': {'name': 'operator_version_hash',
                                    'data_type': 'string',
                                    'description': 'Backend-owned optimistic-concurrency token exposed read-only for '
                                                   'version-fenced operations.',
                                    'semantic_role': 'pool_operator_version_hash',
                                    'data_origin': 'backend',
                                    'write_owner': 'backend',
                                    'readable': True,
                                    'generic_creatable': False,
                                    'generic_writable': False,
                                    'filterable': False,
                                    'searchable': False,
                                    'summary_visible': False,
                                    'detail_visible': True,
                                    'long_text': False,
                                    'required_on_create': False,
                                    'nullable': False,
                                    'enum_values': (),
                                    'relation_target': None,
                                    'aliases': (),
                                    'examples': (),
                                    'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                      'validation.',
                                    'forbidden_uses': (),
                                    'internal': False}},
 'profile': {'id': {'name': 'id',
                    'data_type': 'integer',
                    'description': 'Stable database record identifier exposed read-only in the public record envelope.',
                    'semantic_role': 'profile_id',
                    'data_origin': 'backend',
                    'write_owner': 'backend',
                    'readable': True,
                    'generic_creatable': False,
                    'generic_writable': False,
                    'filterable': True,
                    'searchable': False,
                    'summary_visible': True,
                    'detail_visible': True,
                    'long_text': False,
                    'required_on_create': False,
                    'nullable': False,
                    'enum_values': (),
                    'relation_target': None,
                    'aliases': (),
                    'examples': (),
                    'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                    'forbidden_uses': (),
                    'internal': False},
             'name': {'name': 'name',
                      'data_type': 'string',
                      'description': 'Human-readable name used to identify this business record.',
                      'semantic_role': 'profile_name',
                      'data_origin': 'user_or_system',
                      'write_owner': 'user_or_agent',
                      'readable': True,
                      'generic_creatable': True,
                      'generic_writable': True,
                      'filterable': False,
                      'searchable': True,
                      'summary_visible': True,
                      'detail_visible': True,
                      'long_text': False,
                      'required_on_create': False,
                      'nullable': False,
                      'enum_values': (),
                      'relation_target': None,
                      'aliases': (),
                      'examples': (),
                      'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                      'forbidden_uses': (),
                      'internal': False},
             'school': {'name': 'school',
                        'data_type': 'string',
                        'description': 'Primary school or institution presented in the candidate profile and resume '
                                       'defaults.',
                        'semantic_role': 'profile_school',
                        'data_origin': 'user_or_system',
                        'write_owner': 'user_or_agent',
                        'readable': True,
                        'generic_creatable': True,
                        'generic_writable': True,
                        'filterable': True,
                        'searchable': True,
                        'summary_visible': True,
                        'detail_visible': True,
                        'long_text': False,
                        'required_on_create': False,
                        'nullable': False,
                        'enum_values': (),
                        'relation_target': None,
                        'aliases': (),
                        'examples': (),
                        'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                        'forbidden_uses': (),
                        'internal': False},
             'major': {'name': 'major',
                       'data_type': 'string',
                       'description': 'Primary field of study presented in the candidate profile and resume defaults.',
                       'semantic_role': 'profile_major',
                       'data_origin': 'user_or_system',
                       'write_owner': 'user_or_agent',
                       'readable': True,
                       'generic_creatable': True,
                       'generic_writable': True,
                       'filterable': True,
                       'searchable': True,
                       'summary_visible': True,
                       'detail_visible': True,
                       'long_text': False,
                       'required_on_create': False,
                       'nullable': False,
                       'enum_values': (),
                       'relation_target': None,
                       'aliases': (),
                       'examples': (),
                       'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                       'forbidden_uses': (),
                       'internal': False},
             'degree': {'name': 'degree',
                        'data_type': 'string',
                        'description': 'Academic degree presented in the candidate profile and optional resume '
                                       'content.',
                        'semantic_role': 'profile_degree',
                        'data_origin': 'user_or_system',
                        'write_owner': 'user_or_agent',
                        'readable': True,
                        'generic_creatable': True,
                        'generic_writable': True,
                        'filterable': True,
                        'searchable': False,
                        'summary_visible': True,
                        'detail_visible': True,
                        'long_text': False,
                        'required_on_create': False,
                        'nullable': False,
                        'enum_values': (),
                        'relation_target': None,
                        'aliases': (),
                        'examples': (),
                        'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                        'forbidden_uses': (),
                        'internal': False},
             'gpa': {'name': 'gpa',
                     'data_type': 'string',
                     'description': 'Academic grade-point value retained for resume generation when the user chooses '
                                    'to disclose it.',
                     'semantic_role': 'profile_gpa',
                     'data_origin': 'user_or_system',
                     'write_owner': 'user_or_agent',
                     'readable': True,
                     'generic_creatable': True,
                     'generic_writable': True,
                     'filterable': False,
                     'searchable': False,
                     'summary_visible': False,
                     'detail_visible': True,
                     'long_text': False,
                     'required_on_create': False,
                     'nullable': False,
                     'enum_values': (),
                     'relation_target': None,
                     'aliases': (),
                     'examples': (),
                     'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                     'forbidden_uses': (),
                     'internal': False},
             'email': {'name': 'email',
                       'data_type': 'string',
                       'description': 'Candidate email address available for resume contact blocks and application '
                                      'workflows.',
                       'semantic_role': 'profile_email',
                       'data_origin': 'user_or_system',
                       'write_owner': 'user_or_agent',
                       'readable': True,
                       'generic_creatable': True,
                       'generic_writable': True,
                       'filterable': False,
                       'searchable': False,
                       'summary_visible': False,
                       'detail_visible': True,
                       'long_text': False,
                       'required_on_create': False,
                       'nullable': False,
                       'enum_values': (),
                       'relation_target': None,
                       'aliases': (),
                       'examples': (),
                       'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                       'forbidden_uses': (),
                       'internal': False},
             'phone': {'name': 'phone',
                       'data_type': 'string',
                       'description': 'Candidate phone number available for resume contact blocks and recruiter '
                                      'communication.',
                       'semantic_role': 'profile_phone',
                       'data_origin': 'user_or_system',
                       'write_owner': 'user_or_agent',
                       'readable': True,
                       'generic_creatable': True,
                       'generic_writable': True,
                       'filterable': False,
                       'searchable': False,
                       'summary_visible': False,
                       'detail_visible': True,
                       'long_text': False,
                       'required_on_create': False,
                       'nullable': False,
                       'enum_values': (),
                       'relation_target': None,
                       'aliases': (),
                       'examples': (),
                       'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                       'forbidden_uses': (),
                       'internal': False},
             'wechat': {'name': 'wechat',
                        'data_type': 'string',
                        'description': 'Candidate WeChat identifier available for China-focused recruiting '
                                       'communication.',
                        'semantic_role': 'profile_wechat',
                        'data_origin': 'user_or_system',
                        'write_owner': 'user_or_agent',
                        'readable': True,
                        'generic_creatable': True,
                        'generic_writable': True,
                        'filterable': False,
                        'searchable': False,
                        'summary_visible': False,
                        'detail_visible': True,
                        'long_text': False,
                        'required_on_create': False,
                        'nullable': False,
                        'enum_values': (),
                        'relation_target': None,
                        'aliases': (),
                        'examples': (),
                        'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                        'forbidden_uses': (),
                        'internal': False},
             'headline': {'name': 'headline',
                          'data_type': 'string',
                          'description': 'Concise professional positioning statement used in the profile and resume '
                                         'header.',
                          'semantic_role': 'profile_headline',
                          'data_origin': 'user_or_system',
                          'write_owner': 'user_or_agent',
                          'readable': True,
                          'generic_creatable': True,
                          'generic_writable': True,
                          'filterable': False,
                          'searchable': True,
                          'summary_visible': True,
                          'detail_visible': True,
                          'long_text': False,
                          'required_on_create': False,
                          'nullable': False,
                          'enum_values': (),
                          'relation_target': None,
                          'aliases': (),
                          'examples': (),
                          'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                          'forbidden_uses': (),
                          'internal': False},
             'exit_story': {'name': 'exit_story',
                            'data_type': 'string',
                            'description': 'User-authored career-transition narrative explaining motivation for '
                                           'changing roles.',
                            'semantic_role': 'profile_exit_story',
                            'data_origin': 'user_or_system',
                            'write_owner': 'user_or_agent',
                            'readable': True,
                            'generic_creatable': True,
                            'generic_writable': True,
                            'filterable': False,
                            'searchable': True,
                            'summary_visible': False,
                            'detail_visible': True,
                            'long_text': True,
                            'required_on_create': False,
                            'nullable': False,
                            'enum_values': (),
                            'relation_target': None,
                            'aliases': (),
                            'examples': (),
                            'write_guidance': 'Use only for the registered semantic role and preserve model '
                                              'validation.',
                            'forbidden_uses': (),
                            'internal': False},
             'cross_cutting_advantage': {'name': 'cross_cutting_advantage',
                                         'data_type': 'string',
                                         'description': 'Reusable strength connecting the candidate’s experience '
                                                        'across target roles and industries.',
                                         'semantic_role': 'profile_cross_cutting_advantage',
                                         'data_origin': 'user_or_system',
                                         'write_owner': 'user_or_agent',
                                         'readable': True,
                                         'generic_creatable': True,
                                         'generic_writable': True,
                                         'filterable': False,
                                         'searchable': True,
                                         'summary_visible': False,
                                         'detail_visible': True,
                                         'long_text': True,
                                         'required_on_create': False,
                                         'nullable': False,
                                         'enum_values': (),
                                         'relation_target': None,
                                         'aliases': (),
                                         'examples': (),
                                         'write_guidance': 'Use only for the registered semantic role and preserve '
                                                           'model validation.',
                                         'forbidden_uses': (),
                                         'internal': False},
             'base_info_json': {'name': 'base_info_json',
                                'data_type': 'object',
                                'description': 'Compatibility projection of canonical profile sections used by legacy '
                                               'profile and resume readers.',
                                'semantic_role': 'profile_base_info_json',
                                'data_origin': 'user_or_system',
                                'write_owner': 'user_or_agent',
                                'readable': True,
                                'generic_creatable': True,
                                'generic_writable': True,
                                'filterable': False,
                                'searchable': False,
                                'summary_visible': False,
                                'detail_visible': True,
                                'long_text': False,
                                'required_on_create': False,
                                'nullable': False,
                                'enum_values': (),
                                'relation_target': None,
                                'aliases': (),
                                'examples': (),
                                'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                  'validation.',
                                'forbidden_uses': (),
                                'internal': False},
             'is_default': {'name': 'is_default',
                            'data_type': 'boolean',
                            'description': 'Marks the actor-owned profile selected by default for profile and resume '
                                           'workflows.',
                            'semantic_role': 'profile_is_default',
                            'data_origin': 'user_or_system',
                            'write_owner': 'user_or_agent',
                            'readable': True,
                            'generic_creatable': True,
                            'generic_writable': True,
                            'filterable': True,
                            'searchable': False,
                            'summary_visible': True,
                            'detail_visible': True,
                            'long_text': False,
                            'required_on_create': False,
                            'nullable': False,
                            'enum_values': (),
                            'relation_target': None,
                            'aliases': (),
                            'examples': (),
                            'write_guidance': 'Use only for the registered semantic role and preserve model '
                                              'validation.',
                            'forbidden_uses': (),
                            'internal': False},
             'onboarding_step': {'name': 'onboarding_step',
                                 'data_type': 'integer',
                                 'description': 'Latest completed profile-onboarding milestone used to resume guided '
                                                'setup.',
                                 'semantic_role': 'profile_onboarding_step',
                                 'data_origin': 'user_or_system',
                                 'write_owner': 'user_or_agent',
                                 'readable': True,
                                 'generic_creatable': True,
                                 'generic_writable': True,
                                 'filterable': False,
                                 'searchable': False,
                                 'summary_visible': False,
                                 'detail_visible': True,
                                 'long_text': False,
                                 'required_on_create': False,
                                 'nullable': False,
                                 'enum_values': (),
                                 'relation_target': None,
                                 'aliases': (),
                                 'examples': (),
                                 'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                   'validation.',
                                 'forbidden_uses': (),
                                 'internal': False},
             'created_at': {'name': 'created_at',
                            'data_type': 'datetime',
                            'description': 'Backend-generated creation timestamp exposed read-only in the record '
                                           'envelope.',
                            'semantic_role': 'profile_created_at',
                            'data_origin': 'backend',
                            'write_owner': 'backend',
                            'readable': True,
                            'generic_creatable': False,
                            'generic_writable': False,
                            'filterable': False,
                            'searchable': False,
                            'summary_visible': False,
                            'detail_visible': True,
                            'long_text': False,
                            'required_on_create': False,
                            'nullable': False,
                            'enum_values': (),
                            'relation_target': None,
                            'aliases': (),
                            'examples': (),
                            'write_guidance': 'Use only for the registered semantic role and preserve model '
                                              'validation.',
                            'forbidden_uses': (),
                            'internal': False},
             'updated_at': {'name': 'updated_at',
                            'data_type': 'datetime',
                            'description': 'Backend-generated last-update timestamp exposed read-only in the record '
                                           'envelope.',
                            'semantic_role': 'profile_updated_at',
                            'data_origin': 'backend',
                            'write_owner': 'backend',
                            'readable': True,
                            'generic_creatable': False,
                            'generic_writable': False,
                            'filterable': False,
                            'searchable': False,
                            'summary_visible': False,
                            'detail_visible': True,
                            'long_text': False,
                            'required_on_create': False,
                            'nullable': False,
                            'enum_values': (),
                            'relation_target': None,
                            'aliases': (),
                            'examples': (),
                            'write_guidance': 'Use only for the registered semantic role and preserve model '
                                              'validation.',
                            'forbidden_uses': (),
                            'internal': False},
             'operator_version_hash': {'name': 'operator_version_hash',
                                       'data_type': 'string',
                                       'description': 'Backend-owned optimistic-concurrency token exposed read-only '
                                                      'for version-fenced operations.',
                                       'semantic_role': 'profile_operator_version_hash',
                                       'data_origin': 'backend',
                                       'write_owner': 'backend',
                                       'readable': True,
                                       'generic_creatable': False,
                                       'generic_writable': False,
                                       'filterable': False,
                                       'searchable': False,
                                       'summary_visible': False,
                                       'detail_visible': True,
                                       'long_text': False,
                                       'required_on_create': False,
                                       'nullable': False,
                                       'enum_values': (),
                                       'relation_target': None,
                                       'aliases': (),
                                       'examples': (),
                                       'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                         'validation.',
                                       'forbidden_uses': (),
                                       'internal': False}},
 'profile_target_role': {'id': {'name': 'id',
                                'data_type': 'integer',
                                'description': 'Stable database record identifier exposed read-only in the public '
                                               'record envelope.',
                                'semantic_role': 'profile_target_role_id',
                                'data_origin': 'backend',
                                'write_owner': 'backend',
                                'readable': True,
                                'generic_creatable': False,
                                'generic_writable': False,
                                'filterable': True,
                                'searchable': False,
                                'summary_visible': True,
                                'detail_visible': True,
                                'long_text': False,
                                'required_on_create': False,
                                'nullable': False,
                                'enum_values': (),
                                'relation_target': None,
                                'aliases': (),
                                'examples': (),
                                'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                  'validation.',
                                'forbidden_uses': (),
                                'internal': False},
                         'profile_id': {'name': 'profile_id',
                                        'data_type': 'integer',
                                        'description': 'Actor-owned Profile that owns this target-role or canonical '
                                                       'section record.',
                                        'semantic_role': 'profile_target_role_profile_id',
                                        'data_origin': 'user_or_system',
                                        'write_owner': 'user_or_agent',
                                        'readable': True,
                                        'generic_creatable': True,
                                        'generic_writable': False,
                                        'filterable': True,
                                        'searchable': False,
                                        'summary_visible': True,
                                        'detail_visible': True,
                                        'long_text': False,
                                        'required_on_create': True,
                                        'nullable': False,
                                        'enum_values': (),
                                        'relation_target': 'profile',
                                        'aliases': (),
                                        'examples': (),
                                        'write_guidance': 'Use only for the registered semantic role and preserve '
                                                          'model validation.',
                                        'forbidden_uses': (),
                                        'internal': False},
                         'role_name': {'name': 'role_name',
                                       'data_type': 'string',
                                       'description': 'Target occupation or position the candidate is preparing to '
                                                      'pursue.',
                                       'semantic_role': 'profile_target_role_role_name',
                                       'data_origin': 'user_or_system',
                                       'write_owner': 'user_or_agent',
                                       'readable': True,
                                       'generic_creatable': True,
                                       'generic_writable': True,
                                       'filterable': True,
                                       'searchable': True,
                                       'summary_visible': True,
                                       'detail_visible': True,
                                       'long_text': False,
                                       'required_on_create': True,
                                       'nullable': False,
                                       'enum_values': (),
                                       'relation_target': None,
                                       'aliases': (),
                                       'examples': (),
                                       'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                         'validation.',
                                       'forbidden_uses': (),
                                       'internal': False},
                         'role_level': {'name': 'role_level',
                                        'data_type': 'string',
                                        'description': 'Seniority or career level sought for the target role.',
                                        'semantic_role': 'profile_target_role_role_level',
                                        'data_origin': 'user_or_system',
                                        'write_owner': 'user_or_agent',
                                        'readable': True,
                                        'generic_creatable': True,
                                        'generic_writable': True,
                                        'filterable': False,
                                        'searchable': True,
                                        'summary_visible': False,
                                        'detail_visible': True,
                                        'long_text': False,
                                        'required_on_create': False,
                                        'nullable': False,
                                        'enum_values': (),
                                        'relation_target': None,
                                        'aliases': (),
                                        'examples': (),
                                        'write_guidance': 'Use only for the registered semantic role and preserve '
                                                          'model validation.',
                                        'forbidden_uses': (),
                                        'internal': False},
                         'fit': {'name': 'fit',
                                 'data_type': 'string',
                                 'description': 'Candidate fit assessment and positioning notes for a target role.',
                                 'semantic_role': 'profile_target_role_fit',
                                 'data_origin': 'user_or_system',
                                 'write_owner': 'user_or_agent',
                                 'readable': True,
                                 'generic_creatable': True,
                                 'generic_writable': True,
                                 'filterable': True,
                                 'searchable': False,
                                 'summary_visible': True,
                                 'detail_visible': True,
                                 'long_text': False,
                                 'required_on_create': False,
                                 'nullable': False,
                                 'enum_values': (),
                                 'relation_target': None,
                                 'aliases': (),
                                 'examples': (),
                                 'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                   'validation.',
                                 'forbidden_uses': (),
                                 'internal': False},
                         'created_at': {'name': 'created_at',
                                        'data_type': 'datetime',
                                        'description': 'Backend-generated creation timestamp exposed read-only in the '
                                                       'record envelope.',
                                        'semantic_role': 'profile_target_role_created_at',
                                        'data_origin': 'backend',
                                        'write_owner': 'backend',
                                        'readable': True,
                                        'generic_creatable': False,
                                        'generic_writable': False,
                                        'filterable': False,
                                        'searchable': False,
                                        'summary_visible': False,
                                        'detail_visible': True,
                                        'long_text': False,
                                        'required_on_create': False,
                                        'nullable': False,
                                        'enum_values': (),
                                        'relation_target': None,
                                        'aliases': (),
                                        'examples': (),
                                        'write_guidance': 'Use only for the registered semantic role and preserve '
                                                          'model validation.',
                                        'forbidden_uses': (),
                                        'internal': False},
                         'operator_version_hash': {'name': 'operator_version_hash',
                                                   'data_type': 'string',
                                                   'description': 'Backend-owned optimistic-concurrency token exposed '
                                                                  'read-only for version-fenced operations.',
                                                   'semantic_role': 'profile_target_role_operator_version_hash',
                                                   'data_origin': 'backend',
                                                   'write_owner': 'backend',
                                                   'readable': True,
                                                   'generic_creatable': False,
                                                   'generic_writable': False,
                                                   'filterable': False,
                                                   'searchable': False,
                                                   'summary_visible': False,
                                                   'detail_visible': True,
                                                   'long_text': False,
                                                   'required_on_create': False,
                                                   'nullable': False,
                                                   'enum_values': (),
                                                   'relation_target': None,
                                                   'aliases': (),
                                                   'examples': (),
                                                   'write_guidance': 'Use only for the registered semantic role and '
                                                                     'preserve model validation.',
                                                   'forbidden_uses': (),
                                                   'internal': False}},
 'profile_section': {'id': {'name': 'id',
                            'data_type': 'integer',
                            'description': 'Stable database record identifier exposed read-only in the public record '
                                           'envelope.',
                            'semantic_role': 'profile_section_id',
                            'data_origin': 'backend',
                            'write_owner': 'backend',
                            'readable': True,
                            'generic_creatable': False,
                            'generic_writable': False,
                            'filterable': True,
                            'searchable': False,
                            'summary_visible': True,
                            'detail_visible': True,
                            'long_text': False,
                            'required_on_create': False,
                            'nullable': False,
                            'enum_values': (),
                            'relation_target': None,
                            'aliases': (),
                            'examples': (),
                            'write_guidance': 'Use only for the registered semantic role and preserve model '
                                              'validation.',
                            'forbidden_uses': (),
                            'internal': False},
                     'profile_id': {'name': 'profile_id',
                                    'data_type': 'integer',
                                    'description': 'Actor-owned Profile that owns this target-role or canonical '
                                                   'section record.',
                                    'semantic_role': 'profile_section_profile_id',
                                    'data_origin': 'user_or_system',
                                    'write_owner': 'user_or_agent',
                                    'readable': True,
                                    'generic_creatable': True,
                                    'generic_writable': False,
                                    'filterable': True,
                                    'searchable': False,
                                    'summary_visible': True,
                                    'detail_visible': True,
                                    'long_text': False,
                                    'required_on_create': True,
                                    'nullable': False,
                                    'enum_values': (),
                                    'relation_target': 'profile',
                                    'aliases': (),
                                    'examples': (),
                                    'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                      'validation.',
                                    'forbidden_uses': (),
                                    'internal': False},
                     'section_type': {'name': 'section_type',
                                      'data_type': 'string',
                                      'description': 'Registered content category used to select profile or resume '
                                                     'rendering behavior.',
                                      'semantic_role': 'profile_section_section_type',
                                      'data_origin': 'user_or_system',
                                      'write_owner': 'user_or_agent',
                                      'readable': True,
                                      'generic_creatable': True,
                                      'generic_writable': True,
                                      'filterable': True,
                                      'searchable': False,
                                      'summary_visible': True,
                                      'detail_visible': True,
                                      'long_text': False,
                                      'required_on_create': True,
                                      'nullable': False,
                                      'enum_values': (),
                                      'relation_target': None,
                                      'aliases': (),
                                      'examples': (),
                                      'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                        'validation.',
                                      'forbidden_uses': (),
                                      'internal': False},
                     'parent_id': {'name': 'parent_id',
                                   'data_type': 'integer',
                                   'description': 'Optional parent section used to represent nested content without '
                                                  'losing ownership scope.',
                                   'semantic_role': 'profile_section_parent_id',
                                   'data_origin': 'user_or_system',
                                   'write_owner': 'user_or_agent',
                                   'readable': True,
                                   'generic_creatable': True,
                                   'generic_writable': True,
                                   'filterable': False,
                                   'searchable': False,
                                   'summary_visible': False,
                                   'detail_visible': True,
                                   'long_text': False,
                                   'required_on_create': False,
                                   'nullable': True,
                                   'enum_values': (),
                                   'relation_target': 'profile_section',
                                   'aliases': (),
                                   'examples': (),
                                   'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                     'validation.',
                                   'forbidden_uses': (),
                                   'internal': False},
                     'title': {'name': 'title',
                               'data_type': 'string',
                               'description': 'Human-readable title used to identify this business record.',
                               'semantic_role': 'profile_section_title',
                               'data_origin': 'user_or_system',
                               'write_owner': 'user_or_agent',
                               'readable': True,
                               'generic_creatable': True,
                               'generic_writable': True,
                               'filterable': False,
                               'searchable': True,
                               'summary_visible': True,
                               'detail_visible': True,
                               'long_text': False,
                               'required_on_create': False,
                               'nullable': False,
                               'enum_values': (),
                               'relation_target': None,
                               'aliases': (),
                               'examples': (),
                               'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                 'validation.',
                               'forbidden_uses': (),
                               'internal': False},
                     'sort_order': {'name': 'sort_order',
                                    'data_type': 'integer',
                                    'description': 'Stable user-facing ordering position among peer collections or '
                                                   'content sections.',
                                    'semantic_role': 'profile_section_sort_order',
                                    'data_origin': 'user_or_system',
                                    'write_owner': 'user_or_agent',
                                    'readable': True,
                                    'generic_creatable': True,
                                    'generic_writable': True,
                                    'filterable': False,
                                    'searchable': False,
                                    'summary_visible': False,
                                    'detail_visible': True,
                                    'long_text': False,
                                    'required_on_create': False,
                                    'nullable': False,
                                    'enum_values': (),
                                    'relation_target': None,
                                    'aliases': (),
                                    'examples': (),
                                    'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                      'validation.',
                                    'forbidden_uses': (),
                                    'internal': False},
                     'content_json': {'name': 'content_json',
                                      'data_type': 'object',
                                      'description': 'Canonical structured facts and narrative content rendered for '
                                                     'this profile or resume section.',
                                      'semantic_role': 'profile_section_content_json',
                                      'data_origin': 'user_or_system',
                                      'write_owner': 'user_or_agent',
                                      'readable': True,
                                      'generic_creatable': True,
                                      'generic_writable': True,
                                      'filterable': False,
                                      'searchable': False,
                                      'summary_visible': False,
                                      'detail_visible': True,
                                      'long_text': True,
                                      'required_on_create': False,
                                      'nullable': False,
                                      'enum_values': (),
                                      'relation_target': None,
                                      'aliases': (),
                                      'examples': (),
                                      'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                        'validation.',
                                      'forbidden_uses': (),
                                      'internal': False},
                     'source': {'name': 'source',
                                'data_type': 'string',
                                'description': 'Origin channel or provider retained to preserve provenance for the '
                                               'record.',
                                'semantic_role': 'profile_section_source',
                                'data_origin': 'user_or_system',
                                'write_owner': 'user_or_agent',
                                'readable': True,
                                'generic_creatable': True,
                                'generic_writable': True,
                                'filterable': True,
                                'searchable': False,
                                'summary_visible': True,
                                'detail_visible': True,
                                'long_text': False,
                                'required_on_create': False,
                                'nullable': False,
                                'enum_values': (),
                                'relation_target': None,
                                'aliases': (),
                                'examples': (),
                                'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                  'validation.',
                                'forbidden_uses': (),
                                'internal': False},
                     'confidence': {'name': 'confidence',
                                    'data_type': 'number',
                                    'description': 'Confidence score for extracted or inferred content, used to '
                                                   'prioritize user review.',
                                    'semantic_role': 'profile_section_confidence',
                                    'data_origin': 'user_or_system',
                                    'write_owner': 'user_or_agent',
                                    'readable': True,
                                    'generic_creatable': True,
                                    'generic_writable': True,
                                    'filterable': True,
                                    'searchable': False,
                                    'summary_visible': True,
                                    'detail_visible': True,
                                    'long_text': False,
                                    'required_on_create': False,
                                    'nullable': False,
                                    'enum_values': (),
                                    'relation_target': None,
                                    'aliases': (),
                                    'examples': (),
                                    'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                      'validation.',
                                    'forbidden_uses': (),
                                    'internal': False},
                     'created_at': {'name': 'created_at',
                                    'data_type': 'datetime',
                                    'description': 'Backend-generated creation timestamp exposed read-only in the '
                                                   'record envelope.',
                                    'semantic_role': 'profile_section_created_at',
                                    'data_origin': 'backend',
                                    'write_owner': 'backend',
                                    'readable': True,
                                    'generic_creatable': False,
                                    'generic_writable': False,
                                    'filterable': False,
                                    'searchable': False,
                                    'summary_visible': False,
                                    'detail_visible': True,
                                    'long_text': False,
                                    'required_on_create': False,
                                    'nullable': False,
                                    'enum_values': (),
                                    'relation_target': None,
                                    'aliases': (),
                                    'examples': (),
                                    'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                      'validation.',
                                    'forbidden_uses': (),
                                    'internal': False},
                     'updated_at': {'name': 'updated_at',
                                    'data_type': 'datetime',
                                    'description': 'Backend-generated last-update timestamp exposed read-only in the '
                                                   'record envelope.',
                                    'semantic_role': 'profile_section_updated_at',
                                    'data_origin': 'backend',
                                    'write_owner': 'backend',
                                    'readable': True,
                                    'generic_creatable': False,
                                    'generic_writable': False,
                                    'filterable': False,
                                    'searchable': False,
                                    'summary_visible': False,
                                    'detail_visible': True,
                                    'long_text': False,
                                    'required_on_create': False,
                                    'nullable': False,
                                    'enum_values': (),
                                    'relation_target': None,
                                    'aliases': (),
                                    'examples': (),
                                    'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                      'validation.',
                                    'forbidden_uses': (),
                                    'internal': False},
                     'operator_version_hash': {'name': 'operator_version_hash',
                                               'data_type': 'string',
                                               'description': 'Backend-owned optimistic-concurrency token exposed '
                                                              'read-only for version-fenced operations.',
                                               'semantic_role': 'profile_section_operator_version_hash',
                                               'data_origin': 'backend',
                                               'write_owner': 'backend',
                                               'readable': True,
                                               'generic_creatable': False,
                                               'generic_writable': False,
                                               'filterable': False,
                                               'searchable': False,
                                               'summary_visible': False,
                                               'detail_visible': True,
                                               'long_text': False,
                                               'required_on_create': False,
                                               'nullable': False,
                                               'enum_values': (),
                                               'relation_target': None,
                                               'aliases': (),
                                               'examples': (),
                                               'write_guidance': 'Use only for the registered semantic role and '
                                                                 'preserve model validation.',
                                               'forbidden_uses': (),
                                               'internal': False}},
 'resume_template': {'id': {'name': 'id',
                            'data_type': 'integer',
                            'description': 'Stable database record identifier exposed read-only in the public record '
                                           'envelope.',
                            'semantic_role': 'resume_template_id',
                            'data_origin': 'backend',
                            'write_owner': 'backend',
                            'readable': True,
                            'generic_creatable': False,
                            'generic_writable': False,
                            'filterable': True,
                            'searchable': False,
                            'summary_visible': True,
                            'detail_visible': True,
                            'long_text': False,
                            'required_on_create': False,
                            'nullable': False,
                            'enum_values': (),
                            'relation_target': None,
                            'aliases': (),
                            'examples': (),
                            'write_guidance': 'Use only for the registered semantic role and preserve model '
                                              'validation.',
                            'forbidden_uses': (),
                            'internal': False},
                     'name': {'name': 'name',
                              'data_type': 'string',
                              'description': 'Human-readable name used to identify this business record.',
                              'semantic_role': 'resume_template_name',
                              'data_origin': 'user_or_system',
                              'write_owner': 'user_or_agent',
                              'readable': True,
                              'generic_creatable': False,
                              'generic_writable': False,
                              'filterable': True,
                              'searchable': True,
                              'summary_visible': True,
                              'detail_visible': True,
                              'long_text': False,
                              'required_on_create': False,
                              'nullable': False,
                              'enum_values': (),
                              'relation_target': None,
                              'aliases': (),
                              'examples': (),
                              'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                'validation.',
                              'forbidden_uses': (),
                              'internal': False},
                     'thumbnail_url': {'name': 'thumbnail_url',
                                       'data_type': 'string',
                                       'description': 'Preview image location shown while the user chooses a resume '
                                                      'template.',
                                       'semantic_role': 'resume_template_thumbnail_url',
                                       'data_origin': 'user_or_system',
                                       'write_owner': 'user_or_agent',
                                       'readable': True,
                                       'generic_creatable': False,
                                       'generic_writable': False,
                                       'filterable': False,
                                       'searchable': False,
                                       'summary_visible': True,
                                       'detail_visible': True,
                                       'long_text': False,
                                       'required_on_create': False,
                                       'nullable': False,
                                       'enum_values': (),
                                       'relation_target': None,
                                       'aliases': (),
                                       'examples': (),
                                       'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                         'validation.',
                                       'forbidden_uses': (),
                                       'internal': False},
                     'css_variables': {'name': 'css_variables',
                                       'data_type': 'array',
                                       'description': 'Template design tokens controlling resume typography, spacing, '
                                                      'color, and presentation.',
                                       'semantic_role': 'resume_template_css_variables',
                                       'data_origin': 'user_or_system',
                                       'write_owner': 'user_or_agent',
                                       'readable': True,
                                       'generic_creatable': False,
                                       'generic_writable': False,
                                       'filterable': False,
                                       'searchable': False,
                                       'summary_visible': False,
                                       'detail_visible': True,
                                       'long_text': False,
                                       'required_on_create': False,
                                       'nullable': False,
                                       'enum_values': (),
                                       'relation_target': None,
                                       'aliases': (),
                                       'examples': (),
                                       'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                         'validation.',
                                       'forbidden_uses': (),
                                       'internal': False},
                     'html_layout': {'name': 'html_layout',
                                     'data_type': 'string',
                                     'description': 'Trusted layout definition used to render resume content into the '
                                                    'selected template structure.',
                                     'semantic_role': 'resume_template_html_layout',
                                     'data_origin': 'user_or_system',
                                     'write_owner': 'user_or_agent',
                                     'readable': True,
                                     'generic_creatable': False,
                                     'generic_writable': False,
                                     'filterable': False,
                                     'searchable': False,
                                     'summary_visible': False,
                                     'detail_visible': True,
                                     'long_text': True,
                                     'required_on_create': False,
                                     'nullable': False,
                                     'enum_values': (),
                                     'relation_target': None,
                                     'aliases': (),
                                     'examples': (),
                                     'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                       'validation.',
                                     'forbidden_uses': (),
                                     'internal': False},
                     'is_builtin': {'name': 'is_builtin',
                                    'data_type': 'boolean',
                                    'description': 'Distinguishes product-provided templates from templates created in '
                                                   'an actor-owned workspace.',
                                    'semantic_role': 'resume_template_is_builtin',
                                    'data_origin': 'user_or_system',
                                    'write_owner': 'user_or_agent',
                                    'readable': True,
                                    'generic_creatable': False,
                                    'generic_writable': False,
                                    'filterable': True,
                                    'searchable': False,
                                    'summary_visible': True,
                                    'detail_visible': True,
                                    'long_text': False,
                                    'required_on_create': False,
                                    'nullable': False,
                                    'enum_values': (),
                                    'relation_target': None,
                                    'aliases': (),
                                    'examples': (),
                                    'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                      'validation.',
                                    'forbidden_uses': (),
                                    'internal': False},
                     'created_at': {'name': 'created_at',
                                    'data_type': 'datetime',
                                    'description': 'Backend-generated creation timestamp exposed read-only in the '
                                                   'record envelope.',
                                    'semantic_role': 'resume_template_created_at',
                                    'data_origin': 'backend',
                                    'write_owner': 'backend',
                                    'readable': True,
                                    'generic_creatable': False,
                                    'generic_writable': False,
                                    'filterable': False,
                                    'searchable': False,
                                    'summary_visible': False,
                                    'detail_visible': True,
                                    'long_text': False,
                                    'required_on_create': False,
                                    'nullable': False,
                                    'enum_values': (),
                                    'relation_target': None,
                                    'aliases': (),
                                    'examples': (),
                                    'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                      'validation.',
                                    'forbidden_uses': (),
                                    'internal': False}},
 'resume': {'id': {'name': 'id',
                   'data_type': 'integer',
                   'description': 'Stable database record identifier exposed read-only in the public record envelope.',
                   'semantic_role': 'resume_id',
                   'data_origin': 'backend',
                   'write_owner': 'backend',
                   'readable': True,
                   'generic_creatable': False,
                   'generic_writable': False,
                   'filterable': True,
                   'searchable': False,
                   'summary_visible': True,
                   'detail_visible': True,
                   'long_text': False,
                   'required_on_create': False,
                   'nullable': False,
                   'enum_values': (),
                   'relation_target': None,
                   'aliases': (),
                   'examples': (),
                   'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                   'forbidden_uses': (),
                   'internal': False},
            'user_name': {'name': 'user_name',
                          'data_type': 'string',
                          'description': 'Candidate name rendered in the resume header and exported artifacts.',
                          'semantic_role': 'resume_user_name',
                          'data_origin': 'user_or_system',
                          'write_owner': 'user_or_agent',
                          'readable': True,
                          'generic_creatable': True,
                          'generic_writable': True,
                          'filterable': False,
                          'searchable': True,
                          'summary_visible': True,
                          'detail_visible': True,
                          'long_text': False,
                          'required_on_create': True,
                          'nullable': False,
                          'enum_values': (),
                          'relation_target': None,
                          'aliases': (),
                          'examples': (),
                          'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                          'forbidden_uses': (),
                          'internal': False},
            'title': {'name': 'title',
                      'data_type': 'string',
                      'description': 'Human-readable title used to identify this business record.',
                      'semantic_role': 'resume_title',
                      'data_origin': 'user_or_system',
                      'write_owner': 'user_or_agent',
                      'readable': True,
                      'generic_creatable': True,
                      'generic_writable': True,
                      'filterable': False,
                      'searchable': True,
                      'summary_visible': True,
                      'detail_visible': True,
                      'long_text': False,
                      'required_on_create': False,
                      'nullable': False,
                      'enum_values': (),
                      'relation_target': None,
                      'aliases': (),
                      'examples': (),
                      'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                      'forbidden_uses': (),
                      'internal': False},
            'photo_url': {'name': 'photo_url',
                          'data_type': 'string',
                          'description': 'Candidate portrait asset used only when the selected resume design includes '
                                         'a photo.',
                          'semantic_role': 'resume_photo_url',
                          'data_origin': 'user_or_system',
                          'write_owner': 'user_or_agent',
                          'readable': True,
                          'generic_creatable': True,
                          'generic_writable': True,
                          'filterable': False,
                          'searchable': False,
                          'summary_visible': False,
                          'detail_visible': True,
                          'long_text': False,
                          'required_on_create': False,
                          'nullable': False,
                          'enum_values': (),
                          'relation_target': None,
                          'aliases': (),
                          'examples': (),
                          'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                          'forbidden_uses': (),
                          'internal': False},
            'summary': {'name': 'summary',
                        'data_type': 'string',
                        'description': 'User-approved professional summary tailored for this resume version.',
                        'semantic_role': 'resume_summary',
                        'data_origin': 'user_or_system',
                        'write_owner': 'user_or_agent',
                        'readable': True,
                        'generic_creatable': True,
                        'generic_writable': True,
                        'filterable': False,
                        'searchable': True,
                        'summary_visible': False,
                        'detail_visible': True,
                        'long_text': True,
                        'required_on_create': False,
                        'nullable': False,
                        'enum_values': (),
                        'relation_target': None,
                        'aliases': (),
                        'examples': (),
                        'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                        'forbidden_uses': (),
                        'internal': False},
            'contact_json': {'name': 'contact_json',
                             'data_type': 'object',
                             'description': 'Structured candidate contact methods rendered in resume headers and '
                                            'application artifacts.',
                             'semantic_role': 'resume_contact_json',
                             'data_origin': 'user_or_system',
                             'write_owner': 'user_or_agent',
                             'readable': True,
                             'generic_creatable': True,
                             'generic_writable': True,
                             'filterable': False,
                             'searchable': False,
                             'summary_visible': False,
                             'detail_visible': True,
                             'long_text': False,
                             'required_on_create': False,
                             'nullable': False,
                             'enum_values': (),
                             'relation_target': None,
                             'aliases': (),
                             'examples': (),
                             'write_guidance': 'Use only for the registered semantic role and preserve model '
                                               'validation.',
                             'forbidden_uses': (),
                             'internal': False},
            'template_id': {'name': 'template_id',
                            'data_type': 'integer',
                            'description': 'Selected resume template controlling the rendered layout and design '
                                           'system.',
                            'semantic_role': 'resume_template_id',
                            'data_origin': 'user_or_system',
                            'write_owner': 'user_or_agent',
                            'readable': True,
                            'generic_creatable': True,
                            'generic_writable': True,
                            'filterable': True,
                            'searchable': False,
                            'summary_visible': True,
                            'detail_visible': True,
                            'long_text': False,
                            'required_on_create': False,
                            'nullable': True,
                            'enum_values': (),
                            'relation_target': 'resume_template',
                            'aliases': (),
                            'examples': (),
                            'write_guidance': 'Use only for the registered semantic role and preserve model '
                                              'validation.',
                            'forbidden_uses': (),
                            'internal': False},
            'style_config': {'name': 'style_config',
                             'data_type': 'object',
                             'description': 'Per-resume presentation overrides applied over the selected template '
                                            'design tokens.',
                             'semantic_role': 'resume_style_config',
                             'data_origin': 'user_or_system',
                             'write_owner': 'user_or_agent',
                             'readable': True,
                             'generic_creatable': True,
                             'generic_writable': True,
                             'filterable': False,
                             'searchable': False,
                             'summary_visible': False,
                             'detail_visible': True,
                             'long_text': False,
                             'required_on_create': False,
                             'nullable': False,
                             'enum_values': (),
                             'relation_target': None,
                             'aliases': (),
                             'examples': (),
                             'write_guidance': 'Use only for the registered semantic role and preserve model '
                                               'validation.',
                             'forbidden_uses': (),
                             'internal': False},
            'is_primary': {'name': 'is_primary',
                           'data_type': 'boolean',
                           'description': 'Marks the resume selected by default for application and optimization '
                                          'workflows.',
                           'semantic_role': 'resume_is_primary',
                           'data_origin': 'user_or_system',
                           'write_owner': 'user_or_agent',
                           'readable': True,
                           'generic_creatable': True,
                           'generic_writable': True,
                           'filterable': True,
                           'searchable': False,
                           'summary_visible': True,
                           'detail_visible': True,
                           'long_text': False,
                           'required_on_create': False,
                           'nullable': False,
                           'enum_values': (),
                           'relation_target': None,
                           'aliases': (),
                           'examples': (),
                           'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                           'forbidden_uses': (),
                           'internal': False},
            'language': {'name': 'language',
                         'data_type': 'string',
                         'description': 'Language used for resume content, generated text, and export formatting.',
                         'semantic_role': 'resume_language',
                         'data_origin': 'user_or_system',
                         'write_owner': 'user_or_agent',
                         'readable': True,
                         'generic_creatable': True,
                         'generic_writable': True,
                         'filterable': True,
                         'searchable': False,
                         'summary_visible': True,
                         'detail_visible': True,
                         'long_text': False,
                         'required_on_create': False,
                         'nullable': False,
                         'enum_values': (),
                         'relation_target': None,
                         'aliases': (),
                         'examples': (),
                         'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                         'forbidden_uses': (),
                         'internal': False},
            'source_mode': {'name': 'source_mode',
                            'data_type': 'string',
                            'description': 'Creation pathway such as manual authoring, profile generation, or '
                                           'job-targeted derivation.',
                            'semantic_role': 'resume_source_mode',
                            'data_origin': 'user_or_system',
                            'write_owner': 'user_or_agent',
                            'readable': True,
                            'generic_creatable': True,
                            'generic_writable': True,
                            'filterable': True,
                            'searchable': False,
                            'summary_visible': True,
                            'detail_visible': True,
                            'long_text': False,
                            'required_on_create': False,
                            'nullable': False,
                            'enum_values': (),
                            'relation_target': None,
                            'aliases': (),
                            'examples': (),
                            'write_guidance': 'Use only for the registered semantic role and preserve model '
                                              'validation.',
                            'forbidden_uses': (),
                            'internal': False},
            'source_job_ids': {'name': 'source_job_ids',
                               'data_type': 'array',
                               'description': 'Actor-scoped jobs whose requirements informed this resume version.',
                               'semantic_role': 'resume_source_job_ids',
                               'data_origin': 'user_or_system',
                               'write_owner': 'user_or_agent',
                               'readable': True,
                               'generic_creatable': True,
                               'generic_writable': True,
                               'filterable': False,
                               'searchable': False,
                               'summary_visible': False,
                               'detail_visible': True,
                               'long_text': False,
                               'required_on_create': False,
                               'nullable': True,
                               'enum_values': (),
                               'relation_target': None,
                               'aliases': (),
                               'examples': (),
                               'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                 'validation.',
                               'forbidden_uses': (),
                               'internal': False},
            'source_profile_snapshot': {'name': 'source_profile_snapshot',
                                        'data_type': 'array',
                                        'description': 'Immutable profile facts captured when the resume was '
                                                       'generated.',
                                        'semantic_role': 'resume_source_profile_snapshot',
                                        'data_origin': 'user_or_system',
                                        'write_owner': 'user_or_agent',
                                        'readable': True,
                                        'generic_creatable': True,
                                        'generic_writable': True,
                                        'filterable': False,
                                        'searchable': False,
                                        'summary_visible': False,
                                        'detail_visible': True,
                                        'long_text': False,
                                        'required_on_create': False,
                                        'nullable': True,
                                        'enum_values': (),
                                        'relation_target': None,
                                        'aliases': (),
                                        'examples': (),
                                        'write_guidance': 'Use only for the registered semantic role and preserve '
                                                          'model validation.',
                                        'forbidden_uses': (),
                                        'internal': False},
            'created_at': {'name': 'created_at',
                           'data_type': 'datetime',
                           'description': 'Backend-generated creation timestamp exposed read-only in the record '
                                          'envelope.',
                           'semantic_role': 'resume_created_at',
                           'data_origin': 'backend',
                           'write_owner': 'backend',
                           'readable': True,
                           'generic_creatable': False,
                           'generic_writable': False,
                           'filterable': False,
                           'searchable': False,
                           'summary_visible': False,
                           'detail_visible': True,
                           'long_text': False,
                           'required_on_create': False,
                           'nullable': False,
                           'enum_values': (),
                           'relation_target': None,
                           'aliases': (),
                           'examples': (),
                           'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                           'forbidden_uses': (),
                           'internal': False},
            'updated_at': {'name': 'updated_at',
                           'data_type': 'datetime',
                           'description': 'Backend-generated last-update timestamp exposed read-only in the record '
                                          'envelope.',
                           'semantic_role': 'resume_updated_at',
                           'data_origin': 'backend',
                           'write_owner': 'backend',
                           'readable': True,
                           'generic_creatable': False,
                           'generic_writable': False,
                           'filterable': False,
                           'searchable': False,
                           'summary_visible': False,
                           'detail_visible': True,
                           'long_text': False,
                           'required_on_create': False,
                           'nullable': False,
                           'enum_values': (),
                           'relation_target': None,
                           'aliases': (),
                           'examples': (),
                           'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                           'forbidden_uses': (),
                           'internal': False},
            'operator_version_hash': {'name': 'operator_version_hash',
                                      'data_type': 'string',
                                      'description': 'Backend-owned optimistic-concurrency token exposed read-only for '
                                                     'version-fenced operations.',
                                      'semantic_role': 'resume_operator_version_hash',
                                      'data_origin': 'backend',
                                      'write_owner': 'backend',
                                      'readable': True,
                                      'generic_creatable': False,
                                      'generic_writable': False,
                                      'filterable': False,
                                      'searchable': False,
                                      'summary_visible': False,
                                      'detail_visible': True,
                                      'long_text': False,
                                      'required_on_create': False,
                                      'nullable': False,
                                      'enum_values': (),
                                      'relation_target': None,
                                      'aliases': (),
                                      'examples': (),
                                      'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                        'validation.',
                                      'forbidden_uses': (),
                                      'internal': False}},
 'resume_section': {'id': {'name': 'id',
                           'data_type': 'integer',
                           'description': 'Stable database record identifier exposed read-only in the public record '
                                          'envelope.',
                           'semantic_role': 'resume_section_id',
                           'data_origin': 'backend',
                           'write_owner': 'backend',
                           'readable': True,
                           'generic_creatable': False,
                           'generic_writable': False,
                           'filterable': True,
                           'searchable': False,
                           'summary_visible': True,
                           'detail_visible': True,
                           'long_text': False,
                           'required_on_create': False,
                           'nullable': False,
                           'enum_values': (),
                           'relation_target': None,
                           'aliases': (),
                           'examples': (),
                           'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                           'forbidden_uses': (),
                           'internal': False},
                    'resume_id': {'name': 'resume_id',
                                  'data_type': 'integer',
                                  'description': 'Actor-owned Resume that contains this rendered content section.',
                                  'semantic_role': 'resume_section_resume_id',
                                  'data_origin': 'user_or_system',
                                  'write_owner': 'user_or_agent',
                                  'readable': True,
                                  'generic_creatable': True,
                                  'generic_writable': False,
                                  'filterable': True,
                                  'searchable': False,
                                  'summary_visible': True,
                                  'detail_visible': True,
                                  'long_text': False,
                                  'required_on_create': True,
                                  'nullable': False,
                                  'enum_values': (),
                                  'relation_target': 'resume',
                                  'aliases': (),
                                  'examples': (),
                                  'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                    'validation.',
                                  'forbidden_uses': (),
                                  'internal': False},
                    'section_type': {'name': 'section_type',
                                     'data_type': 'string',
                                     'description': 'Registered content category used to select profile or resume '
                                                    'rendering behavior.',
                                     'semantic_role': 'resume_section_section_type',
                                     'data_origin': 'user_or_system',
                                     'write_owner': 'user_or_agent',
                                     'readable': True,
                                     'generic_creatable': True,
                                     'generic_writable': True,
                                     'filterable': True,
                                     'searchable': False,
                                     'summary_visible': True,
                                     'detail_visible': True,
                                     'long_text': False,
                                     'required_on_create': True,
                                     'nullable': False,
                                     'enum_values': (),
                                     'relation_target': None,
                                     'aliases': (),
                                     'examples': (),
                                     'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                       'validation.',
                                     'forbidden_uses': (),
                                     'internal': False},
                    'sort_order': {'name': 'sort_order',
                                   'data_type': 'integer',
                                   'description': 'Stable user-facing ordering position among peer collections or '
                                                  'content sections.',
                                   'semantic_role': 'resume_section_sort_order',
                                   'data_origin': 'user_or_system',
                                   'write_owner': 'user_or_agent',
                                   'readable': True,
                                   'generic_creatable': True,
                                   'generic_writable': True,
                                   'filterable': False,
                                   'searchable': False,
                                   'summary_visible': True,
                                   'detail_visible': True,
                                   'long_text': False,
                                   'required_on_create': False,
                                   'nullable': False,
                                   'enum_values': (),
                                   'relation_target': None,
                                   'aliases': (),
                                   'examples': (),
                                   'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                     'validation.',
                                   'forbidden_uses': (),
                                   'internal': False},
                    'title': {'name': 'title',
                              'data_type': 'string',
                              'description': 'Human-readable title used to identify this business record.',
                              'semantic_role': 'resume_section_title',
                              'data_origin': 'user_or_system',
                              'write_owner': 'user_or_agent',
                              'readable': True,
                              'generic_creatable': True,
                              'generic_writable': True,
                              'filterable': False,
                              'searchable': True,
                              'summary_visible': True,
                              'detail_visible': True,
                              'long_text': False,
                              'required_on_create': False,
                              'nullable': False,
                              'enum_values': (),
                              'relation_target': None,
                              'aliases': (),
                              'examples': (),
                              'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                'validation.',
                              'forbidden_uses': (),
                              'internal': False},
                    'visible': {'name': 'visible',
                                'data_type': 'boolean',
                                'description': 'Controls whether the section appears in rendered and exported resume '
                                               'output.',
                                'semantic_role': 'resume_section_visible',
                                'data_origin': 'user_or_system',
                                'write_owner': 'user_or_agent',
                                'readable': True,
                                'generic_creatable': True,
                                'generic_writable': True,
                                'filterable': True,
                                'searchable': False,
                                'summary_visible': True,
                                'detail_visible': True,
                                'long_text': False,
                                'required_on_create': False,
                                'nullable': False,
                                'enum_values': (),
                                'relation_target': None,
                                'aliases': (),
                                'examples': (),
                                'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                  'validation.',
                                'forbidden_uses': (),
                                'internal': False},
                    'content_json': {'name': 'content_json',
                                     'data_type': 'array',
                                     'description': 'Ordered structured content items rendered for this resume section.',
                                     'semantic_role': 'resume_section_content_json',
                                     'data_origin': 'user_or_system',
                                     'write_owner': 'user_or_agent',
                                     'readable': True,
                                     'generic_creatable': True,
                                     'generic_writable': True,
                                     'filterable': False,
                                     'searchable': False,
                                     'summary_visible': False,
                                     'detail_visible': True,
                                     'long_text': True,
                                     'required_on_create': False,
                                     'nullable': False,
                                     'enum_values': (),
                                     'relation_target': None,
                                     'aliases': (),
                                     'examples': (),
                                     'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                       'validation.',
                                     'forbidden_uses': (),
                                     'internal': False},
                    'created_at': {'name': 'created_at',
                                   'data_type': 'datetime',
                                   'description': 'Backend-generated creation timestamp exposed read-only in the '
                                                  'record envelope.',
                                   'semantic_role': 'resume_section_created_at',
                                   'data_origin': 'backend',
                                   'write_owner': 'backend',
                                   'readable': True,
                                   'generic_creatable': False,
                                   'generic_writable': False,
                                   'filterable': False,
                                   'searchable': False,
                                   'summary_visible': False,
                                   'detail_visible': True,
                                   'long_text': False,
                                   'required_on_create': False,
                                   'nullable': False,
                                   'enum_values': (),
                                   'relation_target': None,
                                   'aliases': (),
                                   'examples': (),
                                   'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                     'validation.',
                                   'forbidden_uses': (),
                                   'internal': False},
                    'updated_at': {'name': 'updated_at',
                                   'data_type': 'datetime',
                                   'description': 'Backend-generated last-update timestamp exposed read-only in the '
                                                  'record envelope.',
                                   'semantic_role': 'resume_section_updated_at',
                                   'data_origin': 'backend',
                                   'write_owner': 'backend',
                                   'readable': True,
                                   'generic_creatable': False,
                                   'generic_writable': False,
                                   'filterable': False,
                                   'searchable': False,
                                   'summary_visible': False,
                                   'detail_visible': True,
                                   'long_text': False,
                                   'required_on_create': False,
                                   'nullable': False,
                                   'enum_values': (),
                                   'relation_target': None,
                                   'aliases': (),
                                   'examples': (),
                                   'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                     'validation.',
                                   'forbidden_uses': (),
                                   'internal': False},
                    'operator_version_hash': {'name': 'operator_version_hash',
                                              'data_type': 'string',
                                              'description': 'Backend-owned optimistic-concurrency token exposed '
                                                             'read-only for version-fenced operations.',
                                              'semantic_role': 'resume_section_operator_version_hash',
                                              'data_origin': 'backend',
                                              'write_owner': 'backend',
                                              'readable': True,
                                              'generic_creatable': False,
                                              'generic_writable': False,
                                              'filterable': False,
                                              'searchable': False,
                                              'summary_visible': False,
                                              'detail_visible': True,
                                              'long_text': False,
                                              'required_on_create': False,
                                              'nullable': False,
                                              'enum_values': (),
                                              'relation_target': None,
                                              'aliases': (),
                                              'examples': (),
                                              'write_guidance': 'Use only for the registered semantic role and '
                                                                'preserve model validation.',
                                              'forbidden_uses': (),
                                              'internal': False}},
 'interview_notification': {'id': {'name': 'id',
                                   'data_type': 'integer',
                                   'description': 'Stable database record identifier exposed read-only in the public '
                                                  'record envelope.',
                                   'semantic_role': 'interview_notification_id',
                                   'data_origin': 'backend',
                                   'write_owner': 'backend',
                                   'readable': True,
                                   'generic_creatable': False,
                                   'generic_writable': False,
                                   'filterable': True,
                                   'searchable': False,
                                   'summary_visible': True,
                                   'detail_visible': True,
                                   'long_text': False,
                                   'required_on_create': False,
                                   'nullable': False,
                                   'enum_values': (),
                                   'relation_target': None,
                                   'aliases': (),
                                   'examples': (),
                                   'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                     'validation.',
                                   'forbidden_uses': (),
                                   'internal': False},
                            'email_subject': {'name': 'email_subject',
                                              'data_type': 'string',
                                              'description': 'Original email subject retained to identify and display '
                                                             'the interview message.',
                                              'semantic_role': 'interview_notification_email_subject',
                                              'data_origin': 'user_or_system',
                                              'write_owner': 'user_or_agent',
                                              'readable': True,
                                              'generic_creatable': True,
                                              'generic_writable': False,
                                              'filterable': False,
                                              'searchable': True,
                                              'summary_visible': False,
                                              'detail_visible': True,
                                              'long_text': False,
                                              'required_on_create': False,
                                              'nullable': False,
                                              'enum_values': (),
                                              'relation_target': None,
                                              'aliases': (),
                                              'examples': (),
                                              'write_guidance': 'Use only for the registered semantic role and '
                                                                'preserve model validation.',
                                              'forbidden_uses': (),
                                              'internal': False},
                            'email_from': {'name': 'email_from',
                                           'data_type': 'string',
                                           'description': 'Sender identity retained for recruiter and company context.',
                                           'semantic_role': 'interview_notification_email_from',
                                           'data_origin': 'user_or_system',
                                           'write_owner': 'user_or_agent',
                                           'readable': True,
                                           'generic_creatable': True,
                                           'generic_writable': False,
                                           'filterable': False,
                                           'searchable': True,
                                           'summary_visible': False,
                                           'detail_visible': True,
                                           'long_text': False,
                                           'required_on_create': False,
                                           'nullable': False,
                                           'enum_values': (),
                                           'relation_target': None,
                                           'aliases': (),
                                           'examples': (),
                                           'write_guidance': 'Use only for the registered semantic role and preserve '
                                                             'model validation.',
                                           'forbidden_uses': (),
                                           'internal': False},
                            'email_body': {'name': 'email_body',
                                           'data_type': 'string',
                                           'description': 'Original interview email body retained as evidence for '
                                                          'parsed scheduling details.',
                                           'semantic_role': 'interview_notification_email_body',
                                           'data_origin': 'user_or_system',
                                           'write_owner': 'user_or_agent',
                                           'readable': True,
                                           'generic_creatable': True,
                                           'generic_writable': False,
                                           'filterable': False,
                                           'searchable': True,
                                           'summary_visible': False,
                                           'detail_visible': True,
                                           'long_text': True,
                                           'required_on_create': False,
                                           'nullable': False,
                                           'enum_values': (),
                                           'relation_target': None,
                                           'aliases': (),
                                           'examples': (),
                                           'write_guidance': 'Use only for the registered semantic role and preserve '
                                                             'model validation.',
                                           'forbidden_uses': (),
                                           'internal': False},
                            'company': {'name': 'company',
                                        'data_type': 'string',
                                        'description': 'Organization associated with this business record.',
                                        'semantic_role': 'interview_notification_company',
                                        'data_origin': 'user_or_system',
                                        'write_owner': 'user_or_agent',
                                        'readable': True,
                                        'generic_creatable': True,
                                        'generic_writable': True,
                                        'filterable': True,
                                        'searchable': True,
                                        'summary_visible': True,
                                        'detail_visible': True,
                                        'long_text': False,
                                        'required_on_create': False,
                                        'nullable': False,
                                        'enum_values': (),
                                        'relation_target': None,
                                        'aliases': (),
                                        'examples': (),
                                        'write_guidance': 'Use only for the registered semantic role and preserve '
                                                          'model validation.',
                                        'forbidden_uses': (),
                                        'internal': False},
                            'position': {'name': 'position',
                                         'data_type': 'string',
                                         'description': 'Role or position inferred from the interview communication.',
                                         'semantic_role': 'interview_notification_position',
                                         'data_origin': 'user_or_system',
                                         'write_owner': 'user_or_agent',
                                         'readable': True,
                                         'generic_creatable': True,
                                         'generic_writable': True,
                                         'filterable': True,
                                         'searchable': True,
                                         'summary_visible': True,
                                         'detail_visible': True,
                                         'long_text': False,
                                         'required_on_create': False,
                                         'nullable': False,
                                         'enum_values': (),
                                         'relation_target': None,
                                         'aliases': (),
                                         'examples': (),
                                         'write_guidance': 'Use only for the registered semantic role and preserve '
                                                           'model validation.',
                                         'forbidden_uses': (),
                                         'internal': False},
                            'category': {'name': 'category',
                                         'data_type': 'string',
                                         'description': 'Business classification used to group the record for '
                                                        'retrieval, routing, and workflow decisions.',
                                         'semantic_role': 'interview_notification_category',
                                         'data_origin': 'user_or_system',
                                         'write_owner': 'user_or_agent',
                                         'readable': True,
                                         'generic_creatable': True,
                                         'generic_writable': True,
                                         'filterable': True,
                                         'searchable': False,
                                         'summary_visible': True,
                                         'detail_visible': True,
                                         'long_text': False,
                                         'required_on_create': False,
                                         'nullable': False,
                                         'enum_values': (),
                                         'relation_target': None,
                                         'aliases': (),
                                         'examples': (),
                                         'write_guidance': 'Use only for the registered semantic role and preserve '
                                                           'model validation.',
                                         'forbidden_uses': (),
                                         'internal': False},
                            'interview_time': {'name': 'interview_time',
                                               'data_type': 'datetime',
                                               'description': 'Interview timestamp parsed from source communication '
                                                              'for scheduling and reminders.',
                                               'semantic_role': 'interview_notification_interview_time',
                                               'data_origin': 'user_or_system',
                                               'write_owner': 'user_or_agent',
                                               'readable': True,
                                               'generic_creatable': True,
                                               'generic_writable': True,
                                               'filterable': True,
                                               'searchable': False,
                                               'summary_visible': True,
                                               'detail_visible': True,
                                               'long_text': False,
                                               'required_on_create': False,
                                               'nullable': True,
                                               'enum_values': (),
                                               'relation_target': None,
                                               'aliases': (),
                                               'examples': (),
                                               'write_guidance': 'Use only for the registered semantic role and '
                                                                 'preserve model validation.',
                                               'forbidden_uses': (),
                                               'internal': False},
                            'location': {'name': 'location',
                                         'data_type': 'string',
                                         'description': 'Human-readable geographic or remote-work location.',
                                         'semantic_role': 'interview_notification_location',
                                         'data_origin': 'user_or_system',
                                         'write_owner': 'user_or_agent',
                                         'readable': True,
                                         'generic_creatable': True,
                                         'generic_writable': True,
                                         'filterable': False,
                                         'searchable': False,
                                         'summary_visible': True,
                                         'detail_visible': True,
                                         'long_text': False,
                                         'required_on_create': False,
                                         'nullable': False,
                                         'enum_values': (),
                                         'relation_target': None,
                                         'aliases': (),
                                         'examples': (),
                                         'write_guidance': 'Use only for the registered semantic role and preserve '
                                                           'model validation.',
                                         'forbidden_uses': (),
                                         'internal': False},
                            'action_required': {'name': 'action_required',
                                                'data_type': 'string',
                                                'description': 'Indicates that the user still needs to confirm, '
                                                               'schedule, prepare, or respond to the recruiting '
                                                               'message.',
                                                'semantic_role': 'interview_notification_action_required',
                                                'data_origin': 'user_or_system',
                                                'write_owner': 'user_or_agent',
                                                'readable': True,
                                                'generic_creatable': True,
                                                'generic_writable': True,
                                                'filterable': False,
                                                'searchable': True,
                                                'summary_visible': False,
                                                'detail_visible': True,
                                                'long_text': True,
                                                'required_on_create': False,
                                                'nullable': False,
                                                'enum_values': (),
                                                'relation_target': None,
                                                'aliases': (),
                                                'examples': (),
                                                'write_guidance': 'Use only for the registered semantic role and '
                                                                  'preserve model validation.',
                                                'forbidden_uses': (),
                                                'internal': False},
                            'parsed_at': {'name': 'parsed_at',
                                          'data_type': 'datetime',
                                          'description': 'Timestamp when structured details were last extracted from '
                                                         'the source communication.',
                                          'semantic_role': 'interview_notification_parsed_at',
                                          'data_origin': 'user_or_system',
                                          'write_owner': 'user_or_agent',
                                          'readable': True,
                                          'generic_creatable': False,
                                          'generic_writable': False,
                                          'filterable': False,
                                          'searchable': False,
                                          'summary_visible': False,
                                          'detail_visible': True,
                                          'long_text': False,
                                          'required_on_create': False,
                                          'nullable': False,
                                          'enum_values': (),
                                          'relation_target': None,
                                          'aliases': (),
                                          'examples': (),
                                          'write_guidance': 'Use only for the registered semantic role and preserve '
                                                            'model validation.',
                                          'forbidden_uses': (),
                                          'internal': False},
                            'created_at': {'name': 'created_at',
                                           'data_type': 'datetime',
                                           'description': 'Backend-generated creation timestamp exposed read-only in '
                                                          'the record envelope.',
                                           'semantic_role': 'interview_notification_created_at',
                                           'data_origin': 'backend',
                                           'write_owner': 'backend',
                                           'readable': True,
                                           'generic_creatable': False,
                                           'generic_writable': False,
                                           'filterable': False,
                                           'searchable': False,
                                           'summary_visible': False,
                                           'detail_visible': True,
                                           'long_text': False,
                                           'required_on_create': False,
                                           'nullable': False,
                                           'enum_values': (),
                                           'relation_target': None,
                                           'aliases': (),
                                           'examples': (),
                                           'write_guidance': 'Use only for the registered semantic role and preserve '
                                                             'model validation.',
                                           'forbidden_uses': (),
                                           'internal': False},
                            'operator_version_hash': {'name': 'operator_version_hash',
                                                      'data_type': 'string',
                                                      'description': 'Backend-owned optimistic-concurrency token '
                                                                     'exposed read-only for version-fenced operations.',
                                                      'semantic_role': 'interview_notification_operator_version_hash',
                                                      'data_origin': 'backend',
                                                      'write_owner': 'backend',
                                                      'readable': True,
                                                      'generic_creatable': False,
                                                      'generic_writable': False,
                                                      'filterable': False,
                                                      'searchable': False,
                                                      'summary_visible': False,
                                                      'detail_visible': True,
                                                      'long_text': False,
                                                      'required_on_create': False,
                                                      'nullable': False,
                                                      'enum_values': (),
                                                      'relation_target': None,
                                                      'aliases': (),
                                                      'examples': (),
                                                      'write_guidance': 'Use only for the registered semantic role and '
                                                                        'preserve model validation.',
                                                      'forbidden_uses': (),
                                                      'internal': False}},
 'calendar_event': {'id': {'name': 'id',
                           'data_type': 'integer',
                           'description': 'Stable database record identifier exposed read-only in the public record '
                                          'envelope.',
                           'semantic_role': 'calendar_event_id',
                           'data_origin': 'backend',
                           'write_owner': 'backend',
                           'readable': True,
                           'generic_creatable': False,
                           'generic_writable': False,
                           'filterable': True,
                           'searchable': False,
                           'summary_visible': True,
                           'detail_visible': True,
                           'long_text': False,
                           'required_on_create': False,
                           'nullable': False,
                           'enum_values': (),
                           'relation_target': None,
                           'aliases': (),
                           'examples': (),
                           'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                           'forbidden_uses': (),
                           'internal': False},
                    'title': {'name': 'title',
                              'data_type': 'string',
                              'description': 'Human-readable title used to identify this business record.',
                              'semantic_role': 'calendar_event_title',
                              'data_origin': 'user_or_system',
                              'write_owner': 'user_or_agent',
                              'readable': True,
                              'generic_creatable': True,
                              'generic_writable': True,
                              'filterable': False,
                              'searchable': True,
                              'summary_visible': True,
                              'detail_visible': True,
                              'long_text': False,
                              'required_on_create': True,
                              'nullable': False,
                              'enum_values': (),
                              'relation_target': None,
                              'aliases': (),
                              'examples': (),
                              'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                'validation.',
                              'forbidden_uses': (),
                              'internal': False},
                    'description': {'name': 'description',
                                    'data_type': 'string',
                                    'description': 'Business description for this record, interpreted according to the '
                                                   'containing model contract.',
                                    'semantic_role': 'calendar_event_description',
                                    'data_origin': 'user_or_system',
                                    'write_owner': 'user_or_agent',
                                    'readable': True,
                                    'generic_creatable': True,
                                    'generic_writable': True,
                                    'filterable': False,
                                    'searchable': True,
                                    'summary_visible': False,
                                    'detail_visible': True,
                                    'long_text': True,
                                    'required_on_create': False,
                                    'nullable': False,
                                    'enum_values': (),
                                    'relation_target': None,
                                    'aliases': (),
                                    'examples': (),
                                    'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                      'validation.',
                                    'forbidden_uses': (),
                                    'internal': False},
                    'event_type': {'name': 'event_type',
                                   'data_type': 'string',
                                   'description': 'Scheduling category distinguishing interviews from other '
                                                  'application-related calendar events.',
                                   'semantic_role': 'calendar_event_event_type',
                                   'data_origin': 'user_or_system',
                                   'write_owner': 'user_or_agent',
                                   'readable': True,
                                   'generic_creatable': True,
                                   'generic_writable': True,
                                   'filterable': True,
                                   'searchable': False,
                                   'summary_visible': True,
                                   'detail_visible': True,
                                   'long_text': False,
                                   'required_on_create': False,
                                   'nullable': False,
                                   'enum_values': (),
                                   'relation_target': None,
                                   'aliases': (),
                                   'examples': (),
                                   'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                     'validation.',
                                   'forbidden_uses': (),
                                   'internal': False},
                    'start_time': {'name': 'start_time',
                                   'data_type': 'datetime',
                                   'description': 'Confirmed or proposed starting timestamp for the scheduled '
                                                  'recruiting event.',
                                   'semantic_role': 'calendar_event_start_time',
                                   'data_origin': 'user_or_system',
                                   'write_owner': 'user_or_agent',
                                   'readable': True,
                                   'generic_creatable': True,
                                   'generic_writable': True,
                                   'filterable': True,
                                   'searchable': False,
                                   'summary_visible': True,
                                   'detail_visible': True,
                                   'long_text': False,
                                   'required_on_create': True,
                                   'nullable': False,
                                   'enum_values': (),
                                   'relation_target': None,
                                   'aliases': (),
                                   'examples': (),
                                   'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                     'validation.',
                                   'forbidden_uses': (),
                                   'internal': False},
                    'end_time': {'name': 'end_time',
                                 'data_type': 'datetime',
                                 'description': 'Confirmed or proposed ending timestamp for the scheduled recruiting '
                                                'event.',
                                 'semantic_role': 'calendar_event_end_time',
                                 'data_origin': 'user_or_system',
                                 'write_owner': 'user_or_agent',
                                 'readable': True,
                                 'generic_creatable': True,
                                 'generic_writable': True,
                                 'filterable': False,
                                 'searchable': False,
                                 'summary_visible': True,
                                 'detail_visible': True,
                                 'long_text': False,
                                 'required_on_create': False,
                                 'nullable': True,
                                 'enum_values': (),
                                 'relation_target': None,
                                 'aliases': (),
                                 'examples': (),
                                 'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                   'validation.',
                                 'forbidden_uses': (),
                                 'internal': False},
                    'location': {'name': 'location',
                                 'data_type': 'string',
                                 'description': 'Human-readable geographic or remote-work location.',
                                 'semantic_role': 'calendar_event_location',
                                 'data_origin': 'user_or_system',
                                 'write_owner': 'user_or_agent',
                                 'readable': True,
                                 'generic_creatable': True,
                                 'generic_writable': True,
                                 'filterable': False,
                                 'searchable': True,
                                 'summary_visible': True,
                                 'detail_visible': True,
                                 'long_text': False,
                                 'required_on_create': False,
                                 'nullable': False,
                                 'enum_values': (),
                                 'relation_target': None,
                                 'aliases': (),
                                 'examples': (),
                                 'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                   'validation.',
                                 'forbidden_uses': (),
                                 'internal': False},
                    'related_job_id': {'name': 'related_job_id',
                                       'data_type': 'integer',
                                       'description': 'Actor-scoped Job associated with the scheduled recruiting '
                                                      'event.',
                                       'semantic_role': 'calendar_event_related_job_id',
                                       'data_origin': 'user_or_system',
                                       'write_owner': 'user_or_agent',
                                       'readable': True,
                                       'generic_creatable': True,
                                       'generic_writable': True,
                                       'filterable': True,
                                       'searchable': False,
                                       'summary_visible': False,
                                       'detail_visible': True,
                                       'long_text': False,
                                       'required_on_create': False,
                                       'nullable': True,
                                       'enum_values': (),
                                       'relation_target': 'job',
                                       'aliases': (),
                                       'examples': (),
                                       'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                         'validation.',
                                       'forbidden_uses': (),
                                       'internal': False},
                    'related_notification_id': {'name': 'related_notification_id',
                                                'data_type': 'integer',
                                                'description': 'Interview notification that supplied scheduling '
                                                               'evidence for the calendar event.',
                                                'semantic_role': 'calendar_event_related_notification_id',
                                                'data_origin': 'user_or_system',
                                                'write_owner': 'user_or_agent',
                                                'readable': True,
                                                'generic_creatable': True,
                                                'generic_writable': True,
                                                'filterable': True,
                                                'searchable': False,
                                                'summary_visible': False,
                                                'detail_visible': True,
                                                'long_text': False,
                                                'required_on_create': False,
                                                'nullable': True,
                                                'enum_values': (),
                                                'relation_target': 'interview_notification',
                                                'aliases': (),
                                                'examples': (),
                                                'write_guidance': 'Use only for the registered semantic role and '
                                                                  'preserve model validation.',
                                                'forbidden_uses': (),
                                                'internal': False},
                    'created_at': {'name': 'created_at',
                                   'data_type': 'datetime',
                                   'description': 'Backend-generated creation timestamp exposed read-only in the '
                                                  'record envelope.',
                                   'semantic_role': 'calendar_event_created_at',
                                   'data_origin': 'backend',
                                   'write_owner': 'backend',
                                   'readable': True,
                                   'generic_creatable': False,
                                   'generic_writable': False,
                                   'filterable': False,
                                   'searchable': False,
                                   'summary_visible': False,
                                   'detail_visible': True,
                                   'long_text': False,
                                   'required_on_create': False,
                                   'nullable': False,
                                   'enum_values': (),
                                   'relation_target': None,
                                   'aliases': (),
                                   'examples': (),
                                   'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                     'validation.',
                                   'forbidden_uses': (),
                                   'internal': False},
                    'operator_version_hash': {'name': 'operator_version_hash',
                                              'data_type': 'string',
                                              'description': 'Backend-owned optimistic-concurrency token exposed '
                                                             'read-only for version-fenced operations.',
                                              'semantic_role': 'calendar_event_operator_version_hash',
                                              'data_origin': 'backend',
                                              'write_owner': 'backend',
                                              'readable': True,
                                              'generic_creatable': False,
                                              'generic_writable': False,
                                              'filterable': False,
                                              'searchable': False,
                                              'summary_visible': False,
                                              'detail_visible': True,
                                              'long_text': False,
                                              'required_on_create': False,
                                              'nullable': False,
                                              'enum_values': (),
                                              'relation_target': None,
                                              'aliases': (),
                                              'examples': (),
                                              'write_guidance': 'Use only for the registered semantic role and '
                                                                'preserve model validation.',
                                              'forbidden_uses': (),
                                              'internal': False}},
 'application': {'id': {'name': 'id',
                        'data_type': 'integer',
                        'description': 'Stable database record identifier exposed read-only in the public record '
                                       'envelope.',
                        'semantic_role': 'application_id',
                        'data_origin': 'backend',
                        'write_owner': 'backend',
                        'readable': True,
                        'generic_creatable': False,
                        'generic_writable': False,
                        'filterable': True,
                        'searchable': False,
                        'summary_visible': True,
                        'detail_visible': True,
                        'long_text': False,
                        'required_on_create': False,
                        'nullable': False,
                        'enum_values': (),
                        'relation_target': None,
                        'aliases': (),
                        'examples': (),
                        'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                        'forbidden_uses': (),
                        'internal': False},
                 'job_id': {'name': 'job_id',
                            'data_type': 'integer',
                            'description': 'Actor-scoped Job associated with this application or interview-preparation '
                                           'record.',
                            'semantic_role': 'application_job_id',
                            'data_origin': 'user_or_system',
                            'write_owner': 'user_or_agent',
                            'readable': True,
                            'generic_creatable': True,
                            'generic_writable': False,
                            'filterable': True,
                            'searchable': False,
                            'summary_visible': True,
                            'detail_visible': True,
                            'long_text': False,
                            'required_on_create': True,
                            'nullable': False,
                            'enum_values': (),
                            'relation_target': 'job',
                            'aliases': (),
                            'examples': (),
                            'write_guidance': 'Use only for the registered semantic role and preserve model '
                                              'validation.',
                            'forbidden_uses': (),
                            'internal': False},
                 'status': {'name': 'status',
                            'data_type': 'string',
                            'description': 'Registered lifecycle or workflow status for this business record.',
                            'semantic_role': 'application_status',
                            'data_origin': 'user_or_system',
                            'write_owner': 'user_or_agent',
                            'readable': True,
                            'generic_creatable': True,
                            'generic_writable': True,
                            'filterable': True,
                            'searchable': True,
                            'summary_visible': True,
                            'detail_visible': True,
                            'long_text': False,
                            'required_on_create': False,
                            'nullable': False,
                            'enum_values': (),
                            'relation_target': None,
                            'aliases': (),
                            'examples': (),
                            'write_guidance': 'Use only for the registered semantic role and preserve model '
                                              'validation.',
                            'forbidden_uses': (),
                            'internal': False},
                 'cover_letter': {'name': 'cover_letter',
                                  'data_type': 'string',
                                  'description': 'User-approved cover-letter content prepared for a specific '
                                                 'application.',
                                  'semantic_role': 'application_cover_letter',
                                  'data_origin': 'user_or_system',
                                  'write_owner': 'user_or_agent',
                                  'readable': True,
                                  'generic_creatable': True,
                                  'generic_writable': True,
                                  'filterable': False,
                                  'searchable': True,
                                  'summary_visible': False,
                                  'detail_visible': True,
                                  'long_text': True,
                                  'required_on_create': False,
                                  'nullable': False,
                                  'enum_values': (),
                                  'relation_target': None,
                                  'aliases': (),
                                  'examples': (),
                                  'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                    'validation.',
                                  'forbidden_uses': (),
                                  'internal': False},
                 'apply_url': {'name': 'apply_url',
                               'data_type': 'string',
                               'description': 'Direct employer or recruiting-platform endpoint used to submit or '
                                              'revisit an application.',
                               'semantic_role': 'application_apply_url',
                               'data_origin': 'user_or_system',
                               'write_owner': 'user_or_agent',
                               'readable': True,
                               'generic_creatable': True,
                               'generic_writable': True,
                               'filterable': False,
                               'searchable': True,
                               'summary_visible': False,
                               'detail_visible': True,
                               'long_text': False,
                               'required_on_create': False,
                               'nullable': False,
                               'enum_values': (),
                               'relation_target': None,
                               'aliases': (),
                               'examples': (),
                               'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                 'validation.',
                               'forbidden_uses': (),
                               'internal': False},
                 'notes': {'name': 'notes',
                           'data_type': 'string',
                           'description': 'User-authored annotation about the formal application process after a job '
                                          'enters application tracking.',
                           'semantic_role': 'application_process_annotation',
                           'data_origin': 'user',
                           'write_owner': 'user_or_agent',
                           'readable': True,
                           'generic_creatable': True,
                           'generic_writable': True,
                           'filterable': False,
                           'searchable': True,
                           'summary_visible': False,
                           'detail_visible': True,
                           'long_text': True,
                           'required_on_create': False,
                           'nullable': False,
                           'enum_values': (),
                           'relation_target': None,
                           'aliases': (),
                           'examples': (),
                           'write_guidance': 'Use only for the registered semantic role and preserve model validation.',
                           'forbidden_uses': ('Do not use this field for pre-application job-screening annotations.',),
                           'internal': False},
                 'submitted_at': {'name': 'submitted_at',
                                  'data_type': 'datetime',
                                  'description': 'Timestamp when the user records that the application was submitted.',
                                  'semantic_role': 'application_submitted_at',
                                  'data_origin': 'user_or_system',
                                  'write_owner': 'user_or_agent',
                                  'readable': True,
                                  'generic_creatable': True,
                                  'generic_writable': True,
                                  'filterable': True,
                                  'searchable': False,
                                  'summary_visible': True,
                                  'detail_visible': True,
                                  'long_text': False,
                                  'required_on_create': False,
                                  'nullable': True,
                                  'enum_values': (),
                                  'relation_target': None,
                                  'aliases': (),
                                  'examples': (),
                                  'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                    'validation.',
                                  'forbidden_uses': (),
                                  'internal': False},
                 'created_at': {'name': 'created_at',
                                'data_type': 'datetime',
                                'description': 'Backend-generated creation timestamp exposed read-only in the record '
                                               'envelope.',
                                'semantic_role': 'application_created_at',
                                'data_origin': 'backend',
                                'write_owner': 'backend',
                                'readable': True,
                                'generic_creatable': False,
                                'generic_writable': False,
                                'filterable': False,
                                'searchable': False,
                                'summary_visible': False,
                                'detail_visible': True,
                                'long_text': False,
                                'required_on_create': False,
                                'nullable': False,
                                'enum_values': (),
                                'relation_target': None,
                                'aliases': (),
                                'examples': (),
                                'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                  'validation.',
                                'forbidden_uses': (),
                                'internal': False},
                 'updated_at': {'name': 'updated_at',
                                'data_type': 'datetime',
                                'description': 'Backend-generated last-update timestamp exposed read-only in the '
                                               'record envelope.',
                                'semantic_role': 'application_updated_at',
                                'data_origin': 'backend',
                                'write_owner': 'backend',
                                'readable': True,
                                'generic_creatable': False,
                                'generic_writable': False,
                                'filterable': False,
                                'searchable': False,
                                'summary_visible': True,
                                'detail_visible': True,
                                'long_text': False,
                                'required_on_create': False,
                                'nullable': False,
                                'enum_values': (),
                                'relation_target': None,
                                'aliases': (),
                                'examples': (),
                                'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                  'validation.',
                                'forbidden_uses': (),
                                'internal': False},
                 'operator_version_hash': {'name': 'operator_version_hash',
                                           'data_type': 'string',
                                           'description': 'Backend-owned optimistic-concurrency token exposed '
                                                          'read-only for version-fenced operations.',
                                           'semantic_role': 'application_operator_version_hash',
                                           'data_origin': 'backend',
                                           'write_owner': 'backend',
                                           'readable': True,
                                           'generic_creatable': False,
                                           'generic_writable': False,
                                           'filterable': False,
                                           'searchable': False,
                                           'summary_visible': False,
                                           'detail_visible': True,
                                           'long_text': False,
                                           'required_on_create': False,
                                           'nullable': False,
                                           'enum_values': (),
                                           'relation_target': None,
                                           'aliases': (),
                                           'examples': (),
                                           'write_guidance': 'Use only for the registered semantic role and preserve '
                                                             'model validation.',
                                           'forbidden_uses': (),
                                           'internal': False}},
 'application_workspace_settings': {'id': {'name': 'id',
                                           'data_type': 'integer',
                                           'description': 'Stable database record identifier exposed read-only in the '
                                                          'public record envelope.',
                                           'semantic_role': 'application_workspace_settings_id',
                                           'data_origin': 'backend',
                                           'write_owner': 'backend',
                                           'readable': True,
                                           'generic_creatable': False,
                                           'generic_writable': False,
                                           'filterable': True,
                                           'searchable': False,
                                           'summary_visible': True,
                                           'detail_visible': True,
                                           'long_text': False,
                                           'required_on_create': False,
                                           'nullable': False,
                                           'enum_values': (),
                                           'relation_target': None,
                                           'aliases': (),
                                           'examples': (),
                                           'write_guidance': 'Use only for the registered semantic role and preserve '
                                                             'model validation.',
                                           'forbidden_uses': (),
                                           'internal': False},
                                    'auto_row_height': {'name': 'auto_row_height',
                                                        'data_type': 'boolean',
                                                        'description': 'Workspace preference that sizes '
                                                                       'application-table rows to fit visible content.',
                                                        'semantic_role': 'application_workspace_settings_auto_row_height',
                                                        'data_origin': 'user_or_system',
                                                        'write_owner': 'user_or_agent',
                                                        'readable': True,
                                                        'generic_creatable': True,
                                                        'generic_writable': True,
                                                        'filterable': True,
                                                        'searchable': False,
                                                        'summary_visible': True,
                                                        'detail_visible': True,
                                                        'long_text': False,
                                                        'required_on_create': False,
                                                        'nullable': False,
                                                        'enum_values': (),
                                                        'relation_target': None,
                                                        'aliases': (),
                                                        'examples': (),
                                                        'write_guidance': 'Use only for the registered semantic role '
                                                                          'and preserve model validation.',
                                                        'forbidden_uses': (),
                                                        'internal': False},
                                    'auto_column_width': {'name': 'auto_column_width',
                                                          'data_type': 'boolean',
                                                          'description': 'Workspace preference that sizes '
                                                                         'application-table columns for readable '
                                                                         'values.',
                                                          'semantic_role': 'application_workspace_settings_auto_column_width',
                                                          'data_origin': 'user_or_system',
                                                          'write_owner': 'user_or_agent',
                                                          'readable': True,
                                                          'generic_creatable': True,
                                                          'generic_writable': True,
                                                          'filterable': True,
                                                          'searchable': False,
                                                          'summary_visible': True,
                                                          'detail_visible': True,
                                                          'long_text': False,
                                                          'required_on_create': False,
                                                          'nullable': False,
                                                          'enum_values': (),
                                                          'relation_target': None,
                                                          'aliases': (),
                                                          'examples': (),
                                                          'write_guidance': 'Use only for the registered semantic role '
                                                                            'and preserve model validation.',
                                                          'forbidden_uses': (),
                                                          'internal': False},
                                    'delete_subtable_sync_total_default': {'name': 'delete_subtable_sync_total_default',
                                                                           'data_type': 'boolean',
                                                                           'description': 'Default confirmation choice '
                                                                                          'for synchronizing subtable '
                                                                                          'deletions to the total '
                                                                                          'application table.',
                                                                           'semantic_role': 'application_workspace_settings_delete_subtable_sync_total_default',
                                                                           'data_origin': 'user_or_system',
                                                                           'write_owner': 'user_or_agent',
                                                                           'readable': True,
                                                                           'generic_creatable': True,
                                                                           'generic_writable': True,
                                                                           'filterable': True,
                                                                           'searchable': False,
                                                                           'summary_visible': True,
                                                                           'detail_visible': True,
                                                                           'long_text': False,
                                                                           'required_on_create': False,
                                                                           'nullable': False,
                                                                           'enum_values': (),
                                                                           'relation_target': None,
                                                                           'aliases': (),
                                                                           'examples': (),
                                                                           'write_guidance': 'Use only for the '
                                                                                             'registered semantic role '
                                                                                             'and preserve model '
                                                                                             'validation.',
                                                                           'forbidden_uses': (),
                                                                           'internal': False},
                                    'created_at': {'name': 'created_at',
                                                   'data_type': 'datetime',
                                                   'description': 'Backend-generated creation timestamp exposed '
                                                                  'read-only in the record envelope.',
                                                   'semantic_role': 'application_workspace_settings_created_at',
                                                   'data_origin': 'backend',
                                                   'write_owner': 'backend',
                                                   'readable': True,
                                                   'generic_creatable': False,
                                                   'generic_writable': False,
                                                   'filterable': False,
                                                   'searchable': False,
                                                   'summary_visible': False,
                                                   'detail_visible': True,
                                                   'long_text': False,
                                                   'required_on_create': False,
                                                   'nullable': False,
                                                   'enum_values': (),
                                                   'relation_target': None,
                                                   'aliases': (),
                                                   'examples': (),
                                                   'write_guidance': 'Use only for the registered semantic role and '
                                                                     'preserve model validation.',
                                                   'forbidden_uses': (),
                                                   'internal': False},
                                    'updated_at': {'name': 'updated_at',
                                                   'data_type': 'datetime',
                                                   'description': 'Backend-generated last-update timestamp exposed '
                                                                  'read-only in the record envelope.',
                                                   'semantic_role': 'application_workspace_settings_updated_at',
                                                   'data_origin': 'backend',
                                                   'write_owner': 'backend',
                                                   'readable': True,
                                                   'generic_creatable': False,
                                                   'generic_writable': False,
                                                   'filterable': False,
                                                   'searchable': False,
                                                   'summary_visible': False,
                                                   'detail_visible': True,
                                                   'long_text': False,
                                                   'required_on_create': False,
                                                   'nullable': False,
                                                   'enum_values': (),
                                                   'relation_target': None,
                                                   'aliases': (),
                                                   'examples': (),
                                                   'write_guidance': 'Use only for the registered semantic role and '
                                                                     'preserve model validation.',
                                                   'forbidden_uses': (),
                                                   'internal': False}},
 'application_template': {'id': {'name': 'id',
                                 'data_type': 'integer',
                                 'description': 'Stable database record identifier exposed read-only in the public '
                                                'record envelope.',
                                 'semantic_role': 'application_template_id',
                                 'data_origin': 'backend',
                                 'write_owner': 'backend',
                                 'readable': True,
                                 'generic_creatable': False,
                                 'generic_writable': False,
                                 'filterable': True,
                                 'searchable': False,
                                 'summary_visible': True,
                                 'detail_visible': True,
                                 'long_text': False,
                                 'required_on_create': False,
                                 'nullable': False,
                                 'enum_values': (),
                                 'relation_target': None,
                                 'aliases': (),
                                 'examples': (),
                                 'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                   'validation.',
                                 'forbidden_uses': (),
                                 'internal': False},
                          'schema_json': {'name': 'schema_json',
                                          'data_type': 'object',
                                          'description': 'Versioned column, type, validation, and display schema '
                                                         'governing application-table records.',
                                          'semantic_role': 'application_template_schema_json',
                                          'data_origin': 'user_or_system',
                                          'write_owner': 'user_or_agent',
                                          'readable': True,
                                          'generic_creatable': False,
                                          'generic_writable': False,
                                          'filterable': False,
                                          'searchable': False,
                                          'summary_visible': False,
                                          'detail_visible': True,
                                          'long_text': True,
                                          'required_on_create': False,
                                          'nullable': False,
                                          'enum_values': (),
                                          'relation_target': None,
                                          'aliases': (),
                                          'examples': (),
                                          'write_guidance': 'Use only for the registered semantic role and preserve '
                                                            'model validation.',
                                          'forbidden_uses': (),
                                          'internal': False},
                          'created_at': {'name': 'created_at',
                                         'data_type': 'datetime',
                                         'description': 'Backend-generated creation timestamp exposed read-only in the '
                                                        'record envelope.',
                                         'semantic_role': 'application_template_created_at',
                                         'data_origin': 'backend',
                                         'write_owner': 'backend',
                                         'readable': True,
                                         'generic_creatable': False,
                                         'generic_writable': False,
                                         'filterable': False,
                                         'searchable': False,
                                         'summary_visible': False,
                                         'detail_visible': True,
                                         'long_text': False,
                                         'required_on_create': False,
                                         'nullable': False,
                                         'enum_values': (),
                                         'relation_target': None,
                                         'aliases': (),
                                         'examples': (),
                                         'write_guidance': 'Use only for the registered semantic role and preserve '
                                                           'model validation.',
                                         'forbidden_uses': (),
                                         'internal': False},
                          'updated_at': {'name': 'updated_at',
                                         'data_type': 'datetime',
                                         'description': 'Backend-generated last-update timestamp exposed read-only in '
                                                        'the record envelope.',
                                         'semantic_role': 'application_template_updated_at',
                                         'data_origin': 'backend',
                                         'write_owner': 'backend',
                                         'readable': True,
                                         'generic_creatable': False,
                                         'generic_writable': False,
                                         'filterable': True,
                                         'searchable': False,
                                         'summary_visible': True,
                                         'detail_visible': True,
                                         'long_text': False,
                                         'required_on_create': False,
                                         'nullable': False,
                                         'enum_values': (),
                                         'relation_target': None,
                                         'aliases': (),
                                         'examples': (),
                                         'write_guidance': 'Use only for the registered semantic role and preserve '
                                                           'model validation.',
                                         'forbidden_uses': (),
                                         'internal': False}},
 'application_table': {'id': {'name': 'id',
                              'data_type': 'integer',
                              'description': 'Stable database record identifier exposed read-only in the public record '
                                             'envelope.',
                              'semantic_role': 'application_table_id',
                              'data_origin': 'backend',
                              'write_owner': 'backend',
                              'readable': True,
                              'generic_creatable': False,
                              'generic_writable': False,
                              'filterable': True,
                              'searchable': False,
                              'summary_visible': True,
                              'detail_visible': True,
                              'long_text': False,
                              'required_on_create': False,
                              'nullable': False,
                              'enum_values': (),
                              'relation_target': None,
                              'aliases': (),
                              'examples': (),
                              'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                'validation.',
                              'forbidden_uses': (),
                              'internal': False},
                       'name': {'name': 'name',
                                'data_type': 'string',
                                'description': 'Human-readable name used to identify this business record.',
                                'semantic_role': 'application_table_name',
                                'data_origin': 'user_or_system',
                                'write_owner': 'user_or_agent',
                                'readable': True,
                                'generic_creatable': True,
                                'generic_writable': True,
                                'filterable': True,
                                'searchable': True,
                                'summary_visible': True,
                                'detail_visible': True,
                                'long_text': False,
                                'required_on_create': True,
                                'nullable': False,
                                'enum_values': (),
                                'relation_target': None,
                                'aliases': (),
                                'examples': (),
                                'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                  'validation.',
                                'forbidden_uses': (),
                                'internal': False},
                       'is_total': {'name': 'is_total',
                                    'data_type': 'boolean',
                                    'description': 'Distinguishes the actor’s aggregate application table from focused '
                                                   'subtables.',
                                    'semantic_role': 'application_table_is_total',
                                    'data_origin': 'user_or_system',
                                    'write_owner': 'user_or_agent',
                                    'readable': True,
                                    'generic_creatable': True,
                                    'generic_writable': True,
                                    'filterable': True,
                                    'searchable': False,
                                    'summary_visible': True,
                                    'detail_visible': True,
                                    'long_text': False,
                                    'required_on_create': False,
                                    'nullable': False,
                                    'enum_values': (),
                                    'relation_target': None,
                                    'aliases': (),
                                    'examples': (),
                                    'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                      'validation.',
                                    'forbidden_uses': (),
                                    'internal': False},
                       'schema_json': {'name': 'schema_json',
                                       'data_type': 'object',
                                       'description': 'Versioned column, type, validation, and display schema '
                                                      'governing application-table records.',
                                       'semantic_role': 'application_table_schema_json',
                                       'data_origin': 'user_or_system',
                                       'write_owner': 'user_or_agent',
                                       'readable': True,
                                       'generic_creatable': True,
                                       'generic_writable': True,
                                       'filterable': False,
                                       'searchable': False,
                                       'summary_visible': False,
                                       'detail_visible': True,
                                       'long_text': True,
                                       'required_on_create': False,
                                       'nullable': False,
                                       'enum_values': (),
                                       'relation_target': None,
                                       'aliases': (),
                                       'examples': (),
                                       'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                         'validation.',
                                       'forbidden_uses': (),
                                       'internal': False},
                       'created_at': {'name': 'created_at',
                                      'data_type': 'datetime',
                                      'description': 'Backend-generated creation timestamp exposed read-only in the '
                                                     'record envelope.',
                                      'semantic_role': 'application_table_created_at',
                                      'data_origin': 'backend',
                                      'write_owner': 'backend',
                                      'readable': True,
                                      'generic_creatable': False,
                                      'generic_writable': False,
                                      'filterable': False,
                                      'searchable': False,
                                      'summary_visible': False,
                                      'detail_visible': True,
                                      'long_text': False,
                                      'required_on_create': False,
                                      'nullable': False,
                                      'enum_values': (),
                                      'relation_target': None,
                                      'aliases': (),
                                      'examples': (),
                                      'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                        'validation.',
                                      'forbidden_uses': (),
                                      'internal': False},
                       'updated_at': {'name': 'updated_at',
                                      'data_type': 'datetime',
                                      'description': 'Backend-generated last-update timestamp exposed read-only in the '
                                                     'record envelope.',
                                      'semantic_role': 'application_table_updated_at',
                                      'data_origin': 'backend',
                                      'write_owner': 'backend',
                                      'readable': True,
                                      'generic_creatable': False,
                                      'generic_writable': False,
                                      'filterable': False,
                                      'searchable': False,
                                      'summary_visible': True,
                                      'detail_visible': True,
                                      'long_text': False,
                                      'required_on_create': False,
                                      'nullable': False,
                                      'enum_values': (),
                                      'relation_target': None,
                                      'aliases': (),
                                      'examples': (),
                                      'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                        'validation.',
                                      'forbidden_uses': (),
                                      'internal': False},
                       'operator_version_hash': {'name': 'operator_version_hash',
                                                 'data_type': 'string',
                                                 'description': 'Backend-owned optimistic-concurrency token exposed '
                                                                'read-only for version-fenced operations.',
                                                 'semantic_role': 'application_table_operator_version_hash',
                                                 'data_origin': 'backend',
                                                 'write_owner': 'backend',
                                                 'readable': True,
                                                 'generic_creatable': False,
                                                 'generic_writable': False,
                                                 'filterable': False,
                                                 'searchable': False,
                                                 'summary_visible': False,
                                                 'detail_visible': True,
                                                 'long_text': False,
                                                 'required_on_create': False,
                                                 'nullable': False,
                                                 'enum_values': (),
                                                 'relation_target': None,
                                                 'aliases': (),
                                                 'examples': (),
                                                 'write_guidance': 'Use only for the registered semantic role and '
                                                                   'preserve model validation.',
                                                 'forbidden_uses': (),
                                                 'internal': False}},
 'application_record': {'id': {'name': 'id',
                               'data_type': 'integer',
                               'description': 'Stable database record identifier exposed read-only in the public '
                                              'record envelope.',
                               'semantic_role': 'application_record_id',
                               'data_origin': 'backend',
                               'write_owner': 'backend',
                               'readable': True,
                               'generic_creatable': False,
                               'generic_writable': False,
                               'filterable': True,
                               'searchable': False,
                               'summary_visible': True,
                               'detail_visible': True,
                               'long_text': False,
                               'required_on_create': False,
                               'nullable': False,
                               'enum_values': (),
                               'relation_target': None,
                               'aliases': (),
                               'examples': (),
                               'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                 'validation.',
                               'forbidden_uses': (),
                               'internal': False},
                        'job_ref_id': {'name': 'job_ref_id',
                                       'data_type': 'integer',
                                       'description': 'Stable link to the canonical Job when an application row '
                                                      'originates from job discovery.',
                                       'semantic_role': 'application_record_job_ref_id',
                                       'data_origin': 'user_or_system',
                                       'write_owner': 'user_or_agent',
                                       'readable': True,
                                       'generic_creatable': True,
                                       'generic_writable': False,
                                       'filterable': True,
                                       'searchable': False,
                                       'summary_visible': False,
                                       'detail_visible': True,
                                       'long_text': False,
                                       'required_on_create': False,
                                       'nullable': True,
                                       'enum_values': (),
                                       'relation_target': 'job',
                                       'aliases': (),
                                       'examples': (),
                                       'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                         'validation.',
                                       'forbidden_uses': (),
                                       'internal': False},
                        'company_name': {'name': 'company_name',
                                         'data_type': 'string',
                                         'description': 'Employer name displayed in application tracking even when no '
                                                        'canonical Job link exists.',
                                         'semantic_role': 'application_record_company_name',
                                         'data_origin': 'user_or_system',
                                         'write_owner': 'user_or_agent',
                                         'readable': True,
                                         'generic_creatable': True,
                                         'generic_writable': True,
                                         'filterable': True,
                                         'searchable': True,
                                         'summary_visible': True,
                                         'detail_visible': True,
                                         'long_text': False,
                                         'required_on_create': False,
                                         'nullable': False,
                                         'enum_values': (),
                                         'relation_target': None,
                                         'aliases': (),
                                         'examples': (),
                                         'write_guidance': 'Use only for the registered semantic role and preserve '
                                                           'model validation.',
                                         'forbidden_uses': (),
                                         'internal': False},
                        'job_title': {'name': 'job_title',
                                      'data_type': 'string',
                                      'description': 'Role title displayed in application tracking even when no '
                                                     'canonical Job link exists.',
                                      'semantic_role': 'application_record_job_title',
                                      'data_origin': 'user_or_system',
                                      'write_owner': 'user_or_agent',
                                      'readable': True,
                                      'generic_creatable': True,
                                      'generic_writable': True,
                                      'filterable': True,
                                      'searchable': True,
                                      'summary_visible': True,
                                      'detail_visible': True,
                                      'long_text': False,
                                      'required_on_create': False,
                                      'nullable': False,
                                      'enum_values': (),
                                      'relation_target': None,
                                      'aliases': (),
                                      'examples': (),
                                      'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                        'validation.',
                                      'forbidden_uses': (),
                                      'internal': False},
                        'location': {'name': 'location',
                                     'data_type': 'string',
                                     'description': 'Human-readable geographic or remote-work location.',
                                     'semantic_role': 'application_record_location',
                                     'data_origin': 'user_or_system',
                                     'write_owner': 'user_or_agent',
                                     'readable': True,
                                     'generic_creatable': True,
                                     'generic_writable': True,
                                     'filterable': True,
                                     'searchable': True,
                                     'summary_visible': True,
                                     'detail_visible': True,
                                     'long_text': False,
                                     'required_on_create': False,
                                     'nullable': False,
                                     'enum_values': (),
                                     'relation_target': None,
                                     'aliases': (),
                                     'examples': (),
                                     'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                       'validation.',
                                     'forbidden_uses': (),
                                     'internal': False},
                        'job_link': {'name': 'job_link',
                                     'data_type': 'string',
                                     'description': 'Listing or application URL retained with the tracked application '
                                                    'row.',
                                     'semantic_role': 'application_record_job_link',
                                     'data_origin': 'user_or_system',
                                     'write_owner': 'user_or_agent',
                                     'readable': True,
                                     'generic_creatable': True,
                                     'generic_writable': True,
                                     'filterable': False,
                                     'searchable': False,
                                     'summary_visible': False,
                                     'detail_visible': True,
                                     'long_text': False,
                                     'required_on_create': False,
                                     'nullable': False,
                                     'enum_values': (),
                                     'relation_target': None,
                                     'aliases': (),
                                     'examples': (),
                                     'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                       'validation.',
                                     'forbidden_uses': (),
                                     'internal': False},
                        'source': {'name': 'source',
                                   'data_type': 'string',
                                   'description': 'Origin channel or provider retained to preserve provenance for the '
                                                  'record.',
                                   'semantic_role': 'application_record_source',
                                   'data_origin': 'user_or_system',
                                   'write_owner': 'user_or_agent',
                                   'readable': True,
                                   'generic_creatable': True,
                                   'generic_writable': True,
                                   'filterable': True,
                                   'searchable': True,
                                   'summary_visible': True,
                                   'detail_visible': True,
                                   'long_text': False,
                                   'required_on_create': False,
                                   'nullable': False,
                                   'enum_values': (),
                                   'relation_target': None,
                                   'aliases': (),
                                   'examples': (),
                                   'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                     'validation.',
                                   'forbidden_uses': (),
                                   'internal': False},
                        'salary_text': {'name': 'salary_text',
                                        'data_type': 'string',
                                        'description': 'Human-readable compensation text preserved when numeric '
                                                       'normalization is incomplete.',
                                        'semantic_role': 'application_record_salary_text',
                                        'data_origin': 'user_or_system',
                                        'write_owner': 'user_or_agent',
                                        'readable': True,
                                        'generic_creatable': True,
                                        'generic_writable': True,
                                        'filterable': False,
                                        'searchable': True,
                                        'summary_visible': False,
                                        'detail_visible': True,
                                        'long_text': False,
                                        'required_on_create': False,
                                        'nullable': False,
                                        'enum_values': (),
                                        'relation_target': None,
                                        'aliases': (),
                                        'examples': (),
                                        'write_guidance': 'Use only for the registered semantic role and preserve '
                                                          'model validation.',
                                        'forbidden_uses': (),
                                        'internal': False},
                        'updated_at_value': {'name': 'updated_at_value',
                                             'data_type': 'datetime',
                                             'description': 'User-facing last-update value independent of the database '
                                                            'audit timestamp.',
                                             'semantic_role': 'application_record_updated_at_value',
                                             'data_origin': 'user_or_system',
                                             'write_owner': 'user_or_agent',
                                             'readable': True,
                                             'generic_creatable': True,
                                             'generic_writable': True,
                                             'filterable': False,
                                             'searchable': False,
                                             'summary_visible': True,
                                             'detail_visible': True,
                                             'long_text': False,
                                             'required_on_create': False,
                                             'nullable': False,
                                             'enum_values': (),
                                             'relation_target': None,
                                             'aliases': (),
                                             'examples': (),
                                             'write_guidance': 'Use only for the registered semantic role and preserve '
                                                               'model validation.',
                                             'forbidden_uses': (),
                                             'internal': False},
                        'custom_values': {'name': 'custom_values',
                                          'data_type': 'object',
                                          'description': 'Values for user-defined application-table columns, '
                                                         'interpreted against the owning table schema.',
                                          'semantic_role': 'application_record_custom_values',
                                          'data_origin': 'user_or_system',
                                          'write_owner': 'user_or_agent',
                                          'readable': True,
                                          'generic_creatable': True,
                                          'generic_writable': True,
                                          'filterable': False,
                                          'searchable': False,
                                          'summary_visible': False,
                                          'detail_visible': True,
                                          'long_text': True,
                                          'required_on_create': False,
                                          'nullable': False,
                                          'enum_values': (),
                                          'relation_target': None,
                                          'aliases': (),
                                          'examples': (),
                                          'write_guidance': 'Use only for the registered semantic role and preserve '
                                                            'model validation.',
                                          'forbidden_uses': (),
                                          'internal': False},
                        'is_duplicate': {'name': 'is_duplicate',
                                         'data_type': 'boolean',
                                         'description': 'Records the duplicate detector’s decision that this row '
                                                        'represents an existing opportunity.',
                                         'semantic_role': 'application_record_is_duplicate',
                                         'data_origin': 'user_or_system',
                                         'write_owner': 'user_or_agent',
                                         'readable': True,
                                         'generic_creatable': True,
                                         'generic_writable': True,
                                         'filterable': True,
                                         'searchable': False,
                                         'summary_visible': True,
                                         'detail_visible': True,
                                         'long_text': False,
                                         'required_on_create': False,
                                         'nullable': False,
                                         'enum_values': (),
                                         'relation_target': None,
                                         'aliases': (),
                                         'examples': (),
                                         'write_guidance': 'Use only for the registered semantic role and preserve '
                                                           'model validation.',
                                         'forbidden_uses': (),
                                         'internal': False},
                        'duplicate_group': {'name': 'duplicate_group',
                                            'data_type': 'string',
                                            'description': 'Stable cluster identifier for records believed to '
                                                           'represent the same opportunity.',
                                            'semantic_role': 'application_record_duplicate_group',
                                            'data_origin': 'user_or_system',
                                            'write_owner': 'user_or_agent',
                                            'readable': True,
                                            'generic_creatable': True,
                                            'generic_writable': True,
                                            'filterable': True,
                                            'searchable': False,
                                            'summary_visible': False,
                                            'detail_visible': True,
                                            'long_text': False,
                                            'required_on_create': False,
                                            'nullable': False,
                                            'enum_values': (),
                                            'relation_target': None,
                                            'aliases': (),
                                            'examples': (),
                                            'write_guidance': 'Use only for the registered semantic role and preserve '
                                                              'model validation.',
                                            'forbidden_uses': (),
                                            'internal': False},
                        'created_at': {'name': 'created_at',
                                       'data_type': 'datetime',
                                       'description': 'Backend-generated creation timestamp exposed read-only in the '
                                                      'record envelope.',
                                       'semantic_role': 'application_record_created_at',
                                       'data_origin': 'backend',
                                       'write_owner': 'backend',
                                       'readable': True,
                                       'generic_creatable': False,
                                       'generic_writable': False,
                                       'filterable': True,
                                       'searchable': False,
                                       'summary_visible': False,
                                       'detail_visible': True,
                                       'long_text': False,
                                       'required_on_create': False,
                                       'nullable': False,
                                       'enum_values': (),
                                       'relation_target': None,
                                       'aliases': (),
                                       'examples': (),
                                       'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                         'validation.',
                                       'forbidden_uses': (),
                                       'internal': False},
                        'updated_at': {'name': 'updated_at',
                                       'data_type': 'datetime',
                                       'description': 'Backend-generated last-update timestamp exposed read-only in '
                                                      'the record envelope.',
                                       'semantic_role': 'application_record_updated_at',
                                       'data_origin': 'backend',
                                       'write_owner': 'backend',
                                       'readable': True,
                                       'generic_creatable': False,
                                       'generic_writable': False,
                                       'filterable': False,
                                       'searchable': False,
                                       'summary_visible': False,
                                       'detail_visible': True,
                                       'long_text': False,
                                       'required_on_create': False,
                                       'nullable': False,
                                       'enum_values': (),
                                       'relation_target': None,
                                       'aliases': (),
                                       'examples': (),
                                       'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                         'validation.',
                                       'forbidden_uses': (),
                                       'internal': False},
                        'operator_version_hash': {'name': 'operator_version_hash',
                                                  'data_type': 'string',
                                                  'description': 'Backend-owned optimistic-concurrency token exposed '
                                                                 'read-only for version-fenced operations.',
                                                  'semantic_role': 'application_record_operator_version_hash',
                                                  'data_origin': 'backend',
                                                  'write_owner': 'backend',
                                                  'readable': True,
                                                  'generic_creatable': False,
                                                  'generic_writable': False,
                                                  'filterable': False,
                                                  'searchable': False,
                                                  'summary_visible': False,
                                                  'detail_visible': True,
                                                  'long_text': False,
                                                  'required_on_create': False,
                                                  'nullable': False,
                                                  'enum_values': (),
                                                  'relation_target': None,
                                                  'aliases': (),
                                                  'examples': (),
                                                  'write_guidance': 'Use only for the registered semantic role and '
                                                                    'preserve model validation.',
                                                  'forbidden_uses': (),
                                                  'internal': False}},
 'interview_experience': {'id': {'name': 'id',
                                 'data_type': 'integer',
                                 'description': 'Stable database record identifier exposed read-only in the public '
                                                'record envelope.',
                                 'semantic_role': 'interview_experience_id',
                                 'data_origin': 'backend',
                                 'write_owner': 'backend',
                                 'readable': True,
                                 'generic_creatable': False,
                                 'generic_writable': False,
                                 'filterable': True,
                                 'searchable': False,
                                 'summary_visible': True,
                                 'detail_visible': True,
                                 'long_text': False,
                                 'required_on_create': False,
                                 'nullable': False,
                                 'enum_values': (),
                                 'relation_target': None,
                                 'aliases': (),
                                 'examples': (),
                                 'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                   'validation.',
                                 'forbidden_uses': (),
                                 'internal': False},
                          'company': {'name': 'company',
                                      'data_type': 'string',
                                      'description': 'Organization associated with this business record.',
                                      'semantic_role': 'interview_experience_company',
                                      'data_origin': 'user_or_system',
                                      'write_owner': 'user_or_agent',
                                      'readable': True,
                                      'generic_creatable': True,
                                      'generic_writable': True,
                                      'filterable': True,
                                      'searchable': True,
                                      'summary_visible': True,
                                      'detail_visible': True,
                                      'long_text': False,
                                      'required_on_create': True,
                                      'nullable': False,
                                      'enum_values': (),
                                      'relation_target': None,
                                      'aliases': (),
                                      'examples': (),
                                      'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                        'validation.',
                                      'forbidden_uses': (),
                                      'internal': False},
                          'role': {'name': 'role',
                                   'data_type': 'string',
                                   'description': 'Role discussed in the interview experience report.',
                                   'semantic_role': 'interview_experience_role',
                                   'data_origin': 'user_or_system',
                                   'write_owner': 'user_or_agent',
                                   'readable': True,
                                   'generic_creatable': True,
                                   'generic_writable': True,
                                   'filterable': True,
                                   'searchable': True,
                                   'summary_visible': True,
                                   'detail_visible': True,
                                   'long_text': False,
                                   'required_on_create': True,
                                   'nullable': False,
                                   'enum_values': (),
                                   'relation_target': None,
                                   'aliases': (),
                                   'examples': (),
                                   'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                     'validation.',
                                   'forbidden_uses': (),
                                   'internal': False},
                          'source_url': {'name': 'source_url',
                                         'data_type': 'string',
                                         'description': 'Public source location from which the interview experience '
                                                        'was collected.',
                                         'semantic_role': 'interview_experience_source_url',
                                         'data_origin': 'user_or_system',
                                         'write_owner': 'user_or_agent',
                                         'readable': True,
                                         'generic_creatable': True,
                                         'generic_writable': True,
                                         'filterable': False,
                                         'searchable': False,
                                         'summary_visible': False,
                                         'detail_visible': True,
                                         'long_text': False,
                                         'required_on_create': False,
                                         'nullable': True,
                                         'enum_values': (),
                                         'relation_target': None,
                                         'aliases': (),
                                         'examples': (),
                                         'write_guidance': 'Use only for the registered semantic role and preserve '
                                                           'model validation.',
                                         'forbidden_uses': (),
                                         'internal': False},
                          'source_platform': {'name': 'source_platform',
                                              'data_type': 'string',
                                              'description': 'Community or content platform that published the '
                                                             'interview experience.',
                                              'semantic_role': 'interview_experience_source_platform',
                                              'data_origin': 'user_or_system',
                                              'write_owner': 'user_or_agent',
                                              'readable': True,
                                              'generic_creatable': True,
                                              'generic_writable': True,
                                              'filterable': True,
                                              'searchable': False,
                                              'summary_visible': True,
                                              'detail_visible': True,
                                              'long_text': False,
                                              'required_on_create': False,
                                              'nullable': False,
                                              'enum_values': (),
                                              'relation_target': None,
                                              'aliases': (),
                                              'examples': (),
                                              'write_guidance': 'Use only for the registered semantic role and '
                                                                'preserve model validation.',
                                              'forbidden_uses': (),
                                              'internal': False},
                          'raw_text': {'name': 'raw_text',
                                       'data_type': 'string',
                                       'description': 'Source interview narrative retained as evidence for extracted '
                                                      'rounds and questions.',
                                       'semantic_role': 'interview_experience_raw_text',
                                       'data_origin': 'source',
                                       'write_owner': 'source_or_user',
                                       'readable': True,
                                       'generic_creatable': True,
                                       'generic_writable': True,
                                       'filterable': False,
                                       'searchable': True,
                                       'summary_visible': False,
                                       'detail_visible': True,
                                       'long_text': True,
                                       'required_on_create': True,
                                       'nullable': False,
                                       'enum_values': (),
                                       'relation_target': None,
                                       'aliases': (),
                                       'examples': (),
                                       'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                         'validation.',
                                       'forbidden_uses': (),
                                       'internal': False},
                          'interview_rounds': {'name': 'interview_rounds',
                                               'data_type': 'string',
                                               'description': 'Structured sequence of interview stages extracted from '
                                                              'the source narrative.',
                                               'semantic_role': 'interview_experience_interview_rounds',
                                               'data_origin': 'user_or_system',
                                               'write_owner': 'user_or_agent',
                                               'readable': True,
                                               'generic_creatable': True,
                                               'generic_writable': True,
                                               'filterable': False,
                                               'searchable': True,
                                               'summary_visible': False,
                                               'detail_visible': True,
                                               'long_text': True,
                                               'required_on_create': False,
                                               'nullable': True,
                                               'enum_values': (),
                                               'relation_target': None,
                                               'aliases': (),
                                               'examples': (),
                                               'write_guidance': 'Use only for the registered semantic role and '
                                                                 'preserve model validation.',
                                               'forbidden_uses': (),
                                               'internal': False},
                          'job_id': {'name': 'job_id',
                                     'data_type': 'integer',
                                     'description': 'Actor-scoped Job associated with this application or '
                                                    'interview-preparation record.',
                                     'semantic_role': 'interview_experience_job_id',
                                     'data_origin': 'user_or_system',
                                     'write_owner': 'user_or_agent',
                                     'readable': True,
                                     'generic_creatable': True,
                                     'generic_writable': True,
                                     'filterable': True,
                                     'searchable': False,
                                     'summary_visible': True,
                                     'detail_visible': True,
                                     'long_text': False,
                                     'required_on_create': False,
                                     'nullable': True,
                                     'enum_values': (),
                                     'relation_target': 'job',
                                     'aliases': (),
                                     'examples': (),
                                     'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                       'validation.',
                                     'forbidden_uses': (),
                                     'internal': False},
                          'collected_at': {'name': 'collected_at',
                                           'data_type': 'datetime',
                                           'description': 'Timestamp when the source material was collected into the '
                                                          'interview-preparation library.',
                                           'semantic_role': 'interview_experience_collected_at',
                                           'data_origin': 'user_or_system',
                                           'write_owner': 'user_or_agent',
                                           'readable': True,
                                           'generic_creatable': False,
                                           'generic_writable': False,
                                           'filterable': False,
                                           'searchable': False,
                                           'summary_visible': True,
                                           'detail_visible': True,
                                           'long_text': False,
                                           'required_on_create': False,
                                           'nullable': False,
                                           'enum_values': (),
                                           'relation_target': None,
                                           'aliases': (),
                                           'examples': (),
                                           'write_guidance': 'Use only for the registered semantic role and preserve '
                                                             'model validation.',
                                           'forbidden_uses': (),
                                           'internal': False},
                          'operator_version_hash': {'name': 'operator_version_hash',
                                                    'data_type': 'string',
                                                    'description': 'Backend-owned optimistic-concurrency token exposed '
                                                                   'read-only for version-fenced operations.',
                                                    'semantic_role': 'interview_experience_operator_version_hash',
                                                    'data_origin': 'backend',
                                                    'write_owner': 'backend',
                                                    'readable': True,
                                                    'generic_creatable': False,
                                                    'generic_writable': False,
                                                    'filterable': False,
                                                    'searchable': False,
                                                    'summary_visible': False,
                                                    'detail_visible': True,
                                                    'long_text': False,
                                                    'required_on_create': False,
                                                    'nullable': False,
                                                    'enum_values': (),
                                                    'relation_target': None,
                                                    'aliases': (),
                                                    'examples': (),
                                                    'write_guidance': 'Use only for the registered semantic role and '
                                                                      'preserve model validation.',
                                                    'forbidden_uses': (),
                                                    'internal': False}},
 'interview_question': {'id': {'name': 'id',
                               'data_type': 'integer',
                               'description': 'Stable database record identifier exposed read-only in the public '
                                              'record envelope.',
                               'semantic_role': 'interview_question_id',
                               'data_origin': 'backend',
                               'write_owner': 'backend',
                               'readable': True,
                               'generic_creatable': False,
                               'generic_writable': False,
                               'filterable': True,
                               'searchable': False,
                               'summary_visible': True,
                               'detail_visible': True,
                               'long_text': False,
                               'required_on_create': False,
                               'nullable': False,
                               'enum_values': (),
                               'relation_target': None,
                               'aliases': (),
                               'examples': (),
                               'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                 'validation.',
                               'forbidden_uses': (),
                               'internal': False},
                        'experience_id': {'name': 'experience_id',
                                          'data_type': 'integer',
                                          'description': 'Interview-experience source from which the preparation '
                                                         'question was extracted.',
                                          'semantic_role': 'interview_question_experience_id',
                                          'data_origin': 'user_or_system',
                                          'write_owner': 'user_or_agent',
                                          'readable': True,
                                          'generic_creatable': True,
                                          'generic_writable': False,
                                          'filterable': True,
                                          'searchable': False,
                                          'summary_visible': False,
                                          'detail_visible': True,
                                          'long_text': False,
                                          'required_on_create': True,
                                          'nullable': False,
                                          'enum_values': (),
                                          'relation_target': 'interview_experience',
                                          'aliases': (),
                                          'examples': (),
                                          'write_guidance': 'Use only for the registered semantic role and preserve '
                                                            'model validation.',
                                          'forbidden_uses': (),
                                          'internal': False},
                        'question_text': {'name': 'question_text',
                                          'data_type': 'string',
                                          'description': 'Canonical wording of the interview question presented for '
                                                         'preparation.',
                                          'semantic_role': 'interview_question_question_text',
                                          'data_origin': 'user_or_system',
                                          'write_owner': 'user_or_agent',
                                          'readable': True,
                                          'generic_creatable': True,
                                          'generic_writable': True,
                                          'filterable': False,
                                          'searchable': True,
                                          'summary_visible': True,
                                          'detail_visible': True,
                                          'long_text': False,
                                          'required_on_create': True,
                                          'nullable': False,
                                          'enum_values': (),
                                          'relation_target': None,
                                          'aliases': (),
                                          'examples': (),
                                          'write_guidance': 'Use only for the registered semantic role and preserve '
                                                            'model validation.',
                                          'forbidden_uses': (),
                                          'internal': False},
                        'round_type': {'name': 'round_type',
                                       'data_type': 'string',
                                       'description': 'Interview stage in which the preparation question is typically '
                                                      'asked.',
                                       'semantic_role': 'interview_question_round_type',
                                       'data_origin': 'user_or_system',
                                       'write_owner': 'user_or_agent',
                                       'readable': True,
                                       'generic_creatable': True,
                                       'generic_writable': True,
                                       'filterable': True,
                                       'searchable': False,
                                       'summary_visible': True,
                                       'detail_visible': True,
                                       'long_text': False,
                                       'required_on_create': False,
                                       'nullable': False,
                                       'enum_values': (),
                                       'relation_target': None,
                                       'aliases': (),
                                       'examples': (),
                                       'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                         'validation.',
                                       'forbidden_uses': (),
                                       'internal': False},
                        'category': {'name': 'category',
                                     'data_type': 'string',
                                     'description': 'Business classification used to group the record for retrieval, '
                                                    'routing, and workflow decisions.',
                                     'semantic_role': 'interview_question_category',
                                     'data_origin': 'user_or_system',
                                     'write_owner': 'user_or_agent',
                                     'readable': True,
                                     'generic_creatable': True,
                                     'generic_writable': True,
                                     'filterable': True,
                                     'searchable': False,
                                     'summary_visible': True,
                                     'detail_visible': True,
                                     'long_text': False,
                                     'required_on_create': False,
                                     'nullable': False,
                                     'enum_values': (),
                                     'relation_target': None,
                                     'aliases': (),
                                     'examples': (),
                                     'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                       'validation.',
                                     'forbidden_uses': (),
                                     'internal': False},
                        'difficulty': {'name': 'difficulty',
                                       'data_type': 'integer',
                                       'description': 'Estimated question difficulty used to prioritize interview '
                                                      'preparation effort.',
                                       'semantic_role': 'interview_question_difficulty',
                                       'data_origin': 'user_or_system',
                                       'write_owner': 'user_or_agent',
                                       'readable': True,
                                       'generic_creatable': True,
                                       'generic_writable': True,
                                       'filterable': True,
                                       'searchable': False,
                                       'summary_visible': True,
                                       'detail_visible': True,
                                       'long_text': False,
                                       'required_on_create': False,
                                       'nullable': False,
                                       'enum_values': (),
                                       'relation_target': None,
                                       'aliases': (),
                                       'examples': (),
                                       'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                         'validation.',
                                       'forbidden_uses': (),
                                       'internal': False},
                        'frequency': {'name': 'frequency',
                                      'data_type': 'integer',
                                      'description': 'Observed or estimated recurrence used to prioritize commonly '
                                                     'asked interview questions.',
                                      'semantic_role': 'interview_question_frequency',
                                      'data_origin': 'user_or_system',
                                      'write_owner': 'user_or_agent',
                                      'readable': True,
                                      'generic_creatable': True,
                                      'generic_writable': True,
                                      'filterable': False,
                                      'searchable': False,
                                      'summary_visible': True,
                                      'detail_visible': True,
                                      'long_text': False,
                                      'required_on_create': False,
                                      'nullable': False,
                                      'enum_values': (),
                                      'relation_target': None,
                                      'aliases': (),
                                      'examples': (),
                                      'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                        'validation.',
                                      'forbidden_uses': (),
                                      'internal': False},
                        'suggested_answer': {'name': 'suggested_answer',
                                             'data_type': 'string',
                                             'description': 'Draft response retained for review and rehearsal, not '
                                                            'evidence that it was delivered.',
                                             'semantic_role': 'interview_question_suggested_answer',
                                             'data_origin': 'user_or_system',
                                             'write_owner': 'user_or_agent',
                                             'readable': True,
                                             'generic_creatable': True,
                                             'generic_writable': True,
                                             'filterable': False,
                                             'searchable': True,
                                             'summary_visible': False,
                                             'detail_visible': True,
                                             'long_text': True,
                                             'required_on_create': False,
                                             'nullable': True,
                                             'enum_values': (),
                                             'relation_target': None,
                                             'aliases': (),
                                             'examples': (),
                                             'write_guidance': 'Use only for the registered semantic role and preserve '
                                                               'model validation.',
                                             'forbidden_uses': (),
                                             'internal': False},
                        'job_id': {'name': 'job_id',
                                   'data_type': 'integer',
                                   'description': 'Actor-scoped Job associated with this application or '
                                                  'interview-preparation record.',
                                   'semantic_role': 'interview_question_job_id',
                                   'data_origin': 'user_or_system',
                                   'write_owner': 'user_or_agent',
                                   'readable': True,
                                   'generic_creatable': True,
                                   'generic_writable': True,
                                   'filterable': True,
                                   'searchable': False,
                                   'summary_visible': False,
                                   'detail_visible': True,
                                   'long_text': False,
                                   'required_on_create': False,
                                   'nullable': True,
                                   'enum_values': (),
                                   'relation_target': 'job',
                                   'aliases': (),
                                   'examples': (),
                                   'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                     'validation.',
                                   'forbidden_uses': (),
                                   'internal': False},
                        'created_at': {'name': 'created_at',
                                       'data_type': 'datetime',
                                       'description': 'Backend-generated creation timestamp exposed read-only in the '
                                                      'record envelope.',
                                       'semantic_role': 'interview_question_created_at',
                                       'data_origin': 'backend',
                                       'write_owner': 'backend',
                                       'readable': True,
                                       'generic_creatable': False,
                                       'generic_writable': False,
                                       'filterable': False,
                                       'searchable': False,
                                       'summary_visible': False,
                                       'detail_visible': True,
                                       'long_text': False,
                                       'required_on_create': False,
                                       'nullable': False,
                                       'enum_values': (),
                                       'relation_target': None,
                                       'aliases': (),
                                       'examples': (),
                                       'write_guidance': 'Use only for the registered semantic role and preserve model '
                                                         'validation.',
                                       'forbidden_uses': (),
                                       'internal': False},
                        'operator_version_hash': {'name': 'operator_version_hash',
                                                  'data_type': 'string',
                                                  'description': 'Backend-owned optimistic-concurrency token exposed '
                                                                 'read-only for version-fenced operations.',
                                                  'semantic_role': 'interview_question_operator_version_hash',
                                                  'data_origin': 'backend',
                                                  'write_owner': 'backend',
                                                  'readable': True,
                                                  'generic_creatable': False,
                                                  'generic_writable': False,
                                                  'filterable': False,
                                                  'searchable': False,
                                                  'summary_visible': False,
                                                  'detail_visible': True,
                                                  'long_text': False,
                                                  'required_on_create': False,
                                                  'nullable': False,
                                                  'enum_values': (),
                                                  'relation_target': None,
                                                  'aliases': (),
                                                  'examples': (),
                                                  'write_guidance': 'Use only for the registered semantic role and '
                                                                    'preserve model validation.',
                                                  'forbidden_uses': (),
                                                  'internal': False}}}

# ============================================================
# Application lifecycle authority (Part 6 WP4)
# enum_values for application.status and the application_record.apply_status
# FieldSpec derive from the single ApplicationLifecycleSpec registry so the
# model contract cannot drift from the lifecycle authority.
# ============================================================
from app.operator.application_lifecycle import (
    ApplicationLifecycleSpec as _ApplicationLifecycleSpec,
)

FIELD_SPEC_CATALOG["application"]["status"].update(
    enum_values=list(_ApplicationLifecycleSpec.states),
)

FIELD_SPEC_CATALOG["application_record"]["application_id"] = {
    "name": "application_id",
    "data_type": "integer",
    "description": (
        "Binding to the canonical Application lifecycle/material record that this "
        "workspace projection row belongs to (nullable for rows not yet bound)."
    ),
    "semantic_role": "application_record_application_id",
    "data_origin": "user_or_system",
    "write_owner": "user_or_agent",
    "readable": True,
    "generic_creatable": False,
    "generic_writable": False,
    "filterable": True,
    "searchable": False,
    "summary_visible": False,
    "detail_visible": True,
    "long_text": False,
    "required_on_create": False,
    "nullable": True,
    "enum_values": (),
    "relation_target": "application",
    "aliases": (),
    "examples": (),
    "write_guidance": (
        "Backend-owned projection binding; prefer using a canonical action "
        "(ensure_application_for_job) to create or bind it."
    ),
    "forbidden_uses": (),
    "internal": False,
}

FIELD_SPEC_CATALOG["application_record"]["apply_status"] = {
    "name": "apply_status",
    "data_type": "string",
    "description": (
        "Canonical lifecycle stage of the formal application process derived from the "
        "authoritative Application lifecycle (draft/pending/submitted/interview/rejected/offer; "
        "pending corresponds to the workspace label 待投递)."
    ),
    "semantic_role": "application_process_status",
    "data_origin": "user_or_system",
    "write_owner": "user_or_agent",
    "readable": True,
    "generic_creatable": True,
    "generic_writable": True,
    "filterable": False,
    "searchable": False,
    "summary_visible": False,
    "detail_visible": True,
    "long_text": False,
    "required_on_create": False,
    "nullable": False,
    "enum_values": list(_ApplicationLifecycleSpec.states),
    "relation_target": None,
    "aliases": (),
    "examples": (),
    "write_guidance": (
        "Use only lifecycle states from the ApplicationLifecycleSpec authority; the "
        "workspace label for pending is 待投递. Interview round details are a separate "
        "field (interview_round) and must not be stored here."
    ),
    "forbidden_uses": (),
    "internal": False,
}

FIELD_SPEC_CATALOG["application_record"]["custom_values"].update(
    write_guidance=(
        "Values for user-defined application-table columns must follow the owning table "
        "schema (ApplicationTable.schema_json). apply_status is a first-class lifecycle "
        "field registered as application_process_status, not an opaque custom_values "
        "convention."
    ),
    forbidden_uses=(
        "Do not smuggle registered business semantics (apply_status) into arbitrary custom "
        "keys; use the declared apply_status field.",
    ),
)
