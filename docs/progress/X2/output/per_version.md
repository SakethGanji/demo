| Version | Era / made by | v | State | Op | Rows | Outside change | Job 1+2 (crashes, re-run) | Why | After the fix-up + job 3 | ids | Round trip |
|---|---|---|---|---|---|---|---|---|---|---|---|
| support-tickets | sample, pre-DL4 | 1 | active | sample | 20000 |  | migrated | hashes match | migrated | 0..n-1 | equal |
| account-churn | sample, pre-DL4 | 1 | active | sample | 10000 |  | migrated | hashes match | migrated | 0..n-1 | equal |
| credit-default | sample, pre-DL4 | 1 | active | sample | 12000 |  | migrated | hashes match | migrated | 0..n-1 | equal |
| loan-amount | sample, pre-DL4 | 1 | active | sample | 8000 |  | migrated | hashes match | migrated | 0..n-1 | equal |
| transaction-volume | sample, pre-DL4 | 1 | active | sample | 4548 |  | migrated | hashes match | migrated | 0..n-1 | equal |
| card-transactions | sample, pre-DL4 | 1 | active | sample | 6000 |  | migrated | hashes match | migrated | 0..n-1 | equal |
| personal-loans | sample, pre-DL4 | 1 | active | sample | 10000 |  | migrated | hashes match | migrated | 0..n-1 | equal |
| loan-stream | sample, pre-DL4 | 1 | active | sample | 9000 |  | migrated | hashes match | migrated | 0..n-1 | equal |
| loan-losses | sample, pre-DL4 | 1 | active | sample | 20000 |  | migrated | hashes match | migrated | 0..n-1 | equal |
| fair-lending | sample, pre-DL4 | 1 | active | sample | 20000 |  | migrated | hashes match | migrated | 0..n-1 | equal |
| segmented-loans | sample, pre-DL4 | 1 | active | sample | 30000 |  | migrated | hashes match | migrated | 0..n-1 | equal |
| account-attrition | sample, pre-DL4 | 1 | active | sample | 12000 |  | migrated | hashes match | migrated | 0..n-1 | equal |
| credit-rejects | sample, pre-DL4 | 1 | active | sample | 24000 |  | migrated | hashes match | migrated | 0..n-1 | equal |
| complaint-topics | sample, pre-DL4 | 1 | active | sample | 6000 |  | migrated | hashes match | migrated | 0..n-1 | equal |
| ds_3d74a646 | pre-DL4 | 1 | active | upload | 600 | e: pre-DL4 split JSON | migrated | hashes match | migrated | 0..n-1 | equal; bool NA as None |
| ds_fc73795a | pre-DL4 | 2 | active | edit | 582 |  | migrated | hashes match | migrated | root_rows, unsorted, 2 neg | equal; bool NA as None |
| ds_24ed3af0 | pre-DL4 | 3 | active | upload_version | 610 |  | migrated | hashes match | migrated | root_rows, unsorted, 25 neg | equal; bool NA as None |
| ds_e8d86cad | pre-DL4 | 4 | active | data_doctor | 578 |  | migrated | hashes match | migrated | root_rows, unsorted, 2 neg | equal; bool NA as None |
| ds_2e281f4a | pre-DL4 | 1 | active | upload | 400 | a: data.csv cell | skipped | data_changed | migrated | (migrated in job 3) | – |
| ds_3dff21e6 | pre-DL4 | 1 | active | upload | 3000 |  | migrated | hashes match | migrated | 0..n-1 | equal |
| ds_0e8fe4bb | pre-DL4 | 2 | active | sql | 3000 |  | migrated | hashes match | migrated | other [3000..5999] | equal |
| ds_56717055 | pre-DL4 | 1 | active | upload | 300000 |  | migrated | hashes match | migrated | 0..n-1 | equal |
| ds_7fc484d9 | pre-DL4 | 5 | active | clean_labels | 600 | f: data.csv + datasets hashes | migrated | hashes match | migrated | root_rows | equal; bool NA as None |
| ds_94690ce4 | pre-DL4 | 1 | active | upload | 1500 |  | migrated | hashes match | migrated | 0..n-1 | equal |
| ds_5a8a85ca | pre-DL4 | 1 | active | upload | 2000 |  | migrated | hashes match | migrated | 0..n-1 | equal |
| ds_3265bdf9 | pre-DL4 | 2 | active | feature_lab | 2000 |  | migrated | hashes match | migrated | root_rows | equal |
| ds_29f1d44b | DL4 (schema.json) | 1 | active | upload | 800 |  | migrated | hashes match | migrated | 0..n-1 | equal |
| ds_7d4b685c | DL4 (schema.json) | 2 | active | edit | 793 | d: split JSON + split_files.sha256 | skipped | split_changed | migrated | (migrated in job 3) | – |
| ds_28492196 | DL4 (schema.json) | 3 | active | edit | 763 | c: split JSON + split_files.sha256 | migrated | hashes match | migrated | root_rows, unsorted, 1 neg | equal |
| ds_30561b7e | DL4 (schema.json) | 6 | active | edit | 608 |  | migrated | hashes match | migrated | root_rows, unsorted, 25 neg | equal |
| ds_20f9ae63 | DL4 (schema.json) | 4 | active | data_doctor | 791 |  | migrated | hashes match | migrated | root_rows, unsorted, 1 neg | equal |
| ds_ac20c6c3 | DL4 (schema.json) | 5 | active | clean_labels | 800 |  | migrated | hashes match | migrated | root_rows | equal |
| ds_a46339a6 | DL4 (schema.json) | 3 | active | sql | 3000 |  | migrated | hashes match | migrated | other [6000..8999] | equal |
| ds_347be6ec | DL4 (schema.json) | 1 | active | upload | 300 | b: split JSON | skipped | split_changed | migrated | (migrated in job 3) | – |
| ds_15143c8e | DL4 (deleted) | 2 | deleted | edit | 297 |  | migrated | tombstone → manifest, no tables | migrated | – | – |
| ds_fef260b8 | DL4 (schema.json) | 2 | archived | edit | 399 |  | migrated | hashes match | migrated | root_rows | equal |
| ds_5d4f8ded | Parquet child (acfe988, P06) | 6 | active | upload_version | 750 | | not CSV-era | already in the lake | – | from v3's row map | – |
