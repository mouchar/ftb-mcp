-- Schema of a real Family Tree Builder file, for the tables ftb_mcp reads.
--
-- Extracted verbatim from a MyHeritage FTB 8.0.0.8640 file (db_version 1.7) with:
--   SELECT sql FROM sqlite_master WHERE type='table' AND name=?
-- Comments and FOREIGN KEY clauses are FTB's own. tests/make_fixtures.py builds the
-- test fixture on this schema so queries are exercised against genuine column
-- layouts rather than a reduced copy of them.
--
-- Regenerate only if a newer FTB release changes the schema.

CREATE TABLE project_parameters (	
	project_parameter_id INTEGER PRIMARY KEY NOT NULL,  -- project parameter ID comes from framework
	category varchar(255) default '',
	name varchar(255) default '',
	value text default ''
);

CREATE TABLE individual_main_data (
	individual_id INTEGER PRIMARY KEY NOT NULL,
	privacy_level tinyint UNSIGNED default 0,  
	gender char(1) default 'U',  
	last_update INT default 0, -- time_t - Unix Time, the number of seconds since 1970-01-01 00:00:00 UTC
	create_timestamp INT default 0, -- Timestamp by Unix Time, the number of milliseconds since 1970-01-01 00:00:00 UTC
	guid varchar(255) default '',
	is_alive tinyint UNSIGNED default '1',
	research_completed tinyint UNSIGNED default '0',
	dna_test_results TEXT default '',
	delete_flag tinyint UNSIGNED default '0',  -- soft erase flag
	token_on_item_id INT NULL DEFAULT NULL,  -- FK from the token_on_item table
  
	FOREIGN KEY(token_on_item_id) REFERENCES token_on_item(token_on_item_id) ON DELETE SET NULL
);

CREATE TABLE individual_data_set (
	individual_data_set_id INTEGER PRIMARY KEY AUTOINCREMENT,
	delete_flag tinyint UNSIGNED default '0',  -- soft erase flag
    individual_id INT NOT NULL,	
	token_on_item_id INT NULL DEFAULT NULL,
	
	-- if the individual is deleted, erase all it's data sets
	FOREIGN KEY(individual_id) REFERENCES individual_main_data(individual_id) ON DELETE CASCADE ON UPDATE CASCADE,
	FOREIGN KEY(token_on_item_id) REFERENCES token_on_item(token_on_item_id) ON DELETE SET NULL
);

CREATE TABLE individual_lang_data (
    individual_lang_data_id INTEGER PRIMARY KEY AUTOINCREMENT,
	individual_data_set_id INT NOT NULL,
	data_language TINYINT UNSIGNED NOT NULL default '0',
	first_name varchar(255) default '',
	last_name varchar(255) default '',
	'prefix' varchar(255) default '',
	suffix varchar(255) default '',
	nickname varchar(255) default '',
	religious_name varchar(255) default '',
	former_name varchar(255) default '',
	married_surname varchar(255) default '',
	alias_name varchar(255) default '',
	aka varchar(255) default '',
	
	-- if the data set is erased, erase all it's lang data records
	FOREIGN KEY(individual_data_set_id) REFERENCES individual_data_set(individual_data_set_id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE TABLE individual_fact_main_data (
  individual_fact_id INTEGER PRIMARY KEY NOT NULL,  -- fact ID comes from framework
  individual_id INT NOT NULL, -- FK from the individual_main_data table
  token varchar(10) default '', -- this is the original GEDCOM Event\Property tag this fact is based on
  fact_type varchar(100) default '', -- type for custom facts  
  age varchar(255) default '',  
  sorted_date INT default 0, -- a number representing the date for sorting (formatted YYYYMMDD example 20140201 for 1/2/2014)
  lower_bound_search_date INT default 0, -- a number representing the date lower boundary for search (formatted YYYYMMDD example 20140201 for 1/2/2014)
  upper_bound_search_date INT default 0, -- a number representing the date lower boundary for search (formatted YYYYMMDD example 20140201 for 1/2/2014)    
  date varchar(255) default '0000-00-00',  -- free text that can look like "22 NOV 1963" or "JUN 1940" or "BET 1953 AND 1960"
  is_current TINYINT UNSIGNED NOT NULL default '0', -- in case this record is a property marked as current (such as occupation or education)
  privacy_level tinyint UNSIGNED default '0',  
  guid varchar(255) default '',
  place_id INT NULL, -- FK from the places_main_data table
  delete_flag tinyint UNSIGNED default '0',  -- soft erase flag
  token_on_item_id INT NULL DEFAULT NULL,  -- FK from the token_on_item table
  
  -- if the individual is erased, erase all it's facts
  FOREIGN KEY(individual_id) REFERENCES individual_main_data(individual_id) ON DELETE CASCADE ON UPDATE CASCADE ,
  FOREIGN KEY(place_id) REFERENCES places_main_data(place_id)  ON DELETE SET NULL,  
  FOREIGN KEY(token_on_item_id) REFERENCES token_on_item(token_on_item_id) ON DELETE SET NULL
);

CREATE TABLE individual_fact_lang_data (
    individual_fact_lang_id INTEGER PRIMARY KEY AUTOINCREMENT,
	individual_fact_id INT NOT NULL, -- FK from the individual_fact_main_data table
	data_language TINYINT UNSIGNED NOT NULL default '0', -- (pre-defined numbers representing supported FTB languages, 0 = English the default )	
	header text default '',
	cause_of_death varchar(255) default '',
	-- if the individual fact is erased, erase all it's lang data records
	FOREIGN KEY(individual_fact_id) REFERENCES individual_fact_main_data(individual_fact_id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE TABLE family_main_data (
    family_id INTEGER PRIMARY KEY NOT NULL,
	status TINYINT UNSIGNED NOT NULL default '0', -- (pre-defined numbers representing - engaged, divorced, married, separated, widowed, life_partners)
	guid varchar(255) default '',
	delete_flag tinyint UNSIGNED default 0,  -- soft erase flag
	create_timestamp INT default 0, -- Timestamp by Unix Time, the number of milliseconds since 1970-01-01 00:00:00 UTC
	token_on_item_id INT NULL DEFAULT NULL,  -- FK from the token_on_item table
  
	FOREIGN KEY(token_on_item_id) REFERENCES token_on_item(token_on_item_id) ON DELETE SET NULL
);

CREATE TABLE family_individual_connection (
    family_individual_connection_id INTEGER PRIMARY KEY AUTOINCREMENT,
    delete_flag tinyint UNSIGNED default 0,  -- soft erase flag
    create_timestamp INT default 0, -- Timestamp by Unix Time, the number of milliseconds since 1970-01-01 00:00:00 UTC
    family_id INT NOT NULL, -- FK from the family_main_data table
    individual_id INT NOT NULL, -- FK from the individual_main_data table
    individual_role_type TINYINT UNSIGNED NOT NULL default 0, -- (pre-defined numbers representing - natural_child, foster_child, adopted_child, husband, wife)
	child_order_in_family TINYINT NOT NULL default -1, -- in case this member is a child role, this is the user configured order of this child within the family it belongs to(ignored for spouse roles)
    
	-- if the family is erased, erase all it's individual connection child records
	FOREIGN KEY(family_id) REFERENCES family_main_data(family_id) ON DELETE CASCADE ON UPDATE CASCADE ,	
	-- if the individual is erased, erase all it's individual connections
    FOREIGN KEY(individual_id) REFERENCES individual_main_data(individual_id) ON DELETE CASCADE ON UPDATE CASCADE      
);

CREATE TABLE family_fact_main_data (
  family_fact_id INTEGER PRIMARY KEY NOT NULL,  -- fact ID comes from framework
  family_id INT NOT NULL, -- FK from the individual_main_data table
  token varchar(10) default '', -- this is the original GEDCOM Event\Property tag this fact is based on
  fact_type varchar(100) default '', -- type for custom facts  
  spouse_age varchar(255) default '',  
  sorted_date INT default 0, -- a number representing the date for sorting (formatted YYYYMMDD example 20140201 for 1/2/2014)
  lower_bound_search_date INT default 0, -- a number representing the date lower boundary for search (formatted YYYYMMDD example 20140201 for 1/2/2014)
  upper_bound_search_date INT default 0, -- a number representing the date lower boundary for search (formatted YYYYMMDD example 20140201 for 1/2/2014)    
  date varchar(255) default '0000-00-00',  -- free text that can look like "22 NOV 1963" or "JUN 1940" or "BET 1953 AND 1960"
  is_current TINYINT UNSIGNED NOT NULL default '0', -- in case this record is a property marked as current (such as occupation or education)
  privacy_level tinyint UNSIGNED default '0',  
  guid varchar(255) default '',
  place_id INT NULL, -- FK from the places_main_data table
  delete_flag tinyint UNSIGNED default '0',  -- soft erase flag
  token_on_item_id INT NULL DEFAULT NULL,  -- FK from the token_on_item table
  
  -- if the family is erased, erase all it's facts
  FOREIGN KEY(family_id) REFERENCES family_main_data(family_id) ON DELETE CASCADE ON UPDATE CASCADE ,
  FOREIGN KEY(place_id) REFERENCES places_main_data(place_id)  ON DELETE SET NULL,  
  FOREIGN KEY(token_on_item_id) REFERENCES token_on_item(token_on_item_id) ON DELETE SET NULL
);

CREATE TABLE family_fact_lang_data (
    family_fact_lang_id INTEGER PRIMARY KEY AUTOINCREMENT,
	family_fact_id INT NOT NULL, -- FK from the family_fact_main_data table
	data_language TINYINT UNSIGNED NOT NULL default '0', -- (pre-defined numbers representing supported FTB languages, 0 = English the default )	
	header text default '',
	-- if the individual fact is erased, erase all it's lang data records
	FOREIGN KEY(family_fact_id) REFERENCES family_fact_main_data(family_fact_id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE TABLE places_main_data (
    place_id INTEGER PRIMARY KEY AUTOINCREMENT
);

CREATE TABLE places_lang_data (
    place_lang_data_id INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id INT NOT NULL, -- FK from the places_main_data table
    data_language TINYINT UNSIGNED NOT NULL default '0', -- (pre-defined numbers representing supported FTB languages, 0 = English the default )	
    place text default '',
    
	-- if the main place record is erased, erase all it's lang records
    FOREIGN KEY(place_id) REFERENCES places_main_data(place_id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE TABLE token_on_item (
    token_on_item_id  INTEGER PRIMARY KEY NOT NULL,
    entity_id INT NOT NULL default 0,
    item_type TINYINT UNSIGNED NOT NULL default 0 -- (pre-defined numbers representing supported Genealogy entity types, 0 = default no type defined)
);

CREATE TABLE note_main_data (
    note_id INTEGER PRIMARY KEY NOT NULL,  -- note ID comes from framework
	guid varchar(255) default '',
	special_note_key varchar(10) default '', -- if exists, this is the GEDCOM special note key, making the note an extension to the entity it is associated to
	privacy_level tinyint UNSIGNED default 0,
	delete_flag tinyint UNSIGNED default '0',  -- soft erase flag
	token_on_item_id INT NULL DEFAULT NULL,  -- FK from the token_on_item table
	
	FOREIGN KEY(token_on_item_id) REFERENCES token_on_item(token_on_item_id) ON DELETE SET NULL
);

CREATE TABLE note_lang_data (
    note_lang_data_id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id INT NOT NULL, -- FK from the note_main_data table
    data_language TINYINT UNSIGNED NOT NULL default '0', -- (pre-defined numbers representing supported FTB languages, 0 = English the default )	
    note_text text default '',
    
    -- if the main note record is erased, erase all it's lang records
    FOREIGN KEY(note_id) REFERENCES note_main_data(note_id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE TABLE note_to_item_connection (
    note_to_item_connection_id INTEGER PRIMARY KEY AUTOINCREMENT,	
    note_id INT NOT NULL, -- FK from the note_main_data table
	delete_flag tinyint UNSIGNED default '0',  -- soft erase flag
	external_token_on_item_id INT NULL DEFAULT NULL,  -- External Token the entity this citation refers to, FK from the token_on_item table
	
    -- if the main note record is erased, erase all it's dependant associative records, cannot connect tokens to invalid notes
    FOREIGN KEY(note_id) REFERENCES note_main_data(note_id) ON DELETE CASCADE ON UPDATE CASCADE,
	
	-- if the token on item is erased, erase all it's dependant associative records, cannot connect notes to invalid tokens
	FOREIGN KEY(external_token_on_item_id) REFERENCES token_on_item(token_on_item_id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE TABLE source_main_data 
(
	source_id INTEGER PRIMARY KEY NOT NULL,  -- ID comes from framework
	create_timestamp INT default 0, -- timestamp by Unix Time, the number of milliseconds since 1970-01-01 00:00:00 UTC
	delete_flag tinyint UNSIGNED default 0,  -- soft erase flag
	token_on_item_id INT NULL DEFAULT NULL,  -- FK from the token_on_item table
	repository_id INT NULL DEFAULT NULL,	-- FK from the repository_main_data table
	
	-- if the referenced repository record is erased, set null
    FOREIGN KEY(repository_id) REFERENCES repository_main_data(repository_id) ON DELETE SET NULL ON UPDATE CASCADE,
	FOREIGN KEY(token_on_item_id) REFERENCES token_on_item(token_on_item_id) ON DELETE SET NULL
);

CREATE TABLE source_lang_data
(
	source_lang_data_id INTEGER PRIMARY KEY AUTOINCREMENT,
	source_id INT NOT NULL,	-- FK from the source_main_data table
	data_language TINYINT UNSIGNED NOT NULL default '0', -- (pre-defined numbers representing supported FTB languages, 0 = English the default )	
	title varchar(255) default '',
	abbreviation varchar(255) default '',
	author varchar(255) default '',
	publisher varchar(255) default '',
	agency varchar(255) default '',
	text text default '',
	type varchar(255) default '',
	media varchar(255) default '', 
	-- if the referenced source record is erased, erase all it's dependant associative records, cannot connect to invalid records
    FOREIGN KEY(source_id) REFERENCES source_main_data(source_id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE TABLE citation_main_data 
(
	citation_id INTEGER PRIMARY KEY NOT NULL,  -- ID comes from framework
	source_id INT NULL DEFAULT NULL,	-- FK from the source_main_data table (NULL allowed)
	page varchar(255) default '',
	confidence tinyint default -1, -- (enum for confidence level [-1=unspecified])  
	event_type varchar(255) default '',
	event_role varchar(255) default '',
	date varchar(255) default '',  -- free text that can look like "22 NOV 1963" or "JUN 1940" or "BET 1953 AND 1960"
	sorted_date INT default 0, -- a number representing the date for sorting (formatted YYYYMMDD example 20140201 for 1/2/2014)
	lower_bound_search_date INT default 0, -- a number representing the date lower boundary for search (formatted YYYYMMDD example 20140201 for 1/2/2014)
	upper_bound_search_date INT default 0, -- a number representing the date lower boundary for search (formatted YYYYMMDD example 20140201 for 1/2/2014)		
	delete_flag tinyint UNSIGNED default 0,  -- soft erase flag
	token_on_item_id INT NULL DEFAULT NULL,  -- FK from the token_on_item table
	external_token_on_item_id INT NULL DEFAULT NULL,  -- External Token the entity this citation refers to, FK from the token_on_item table
	
	-- if the referenced source record is erased, erase all it's dependant associative records, cannot connect to invalid records
    FOREIGN KEY(source_id) REFERENCES source_main_data(source_id) ON DELETE CASCADE ON UPDATE CASCADE,
	
	-- if the referenced external entity token record is erased, erase all it's dependant associative records, cannot connect to invalid entity token
	FOREIGN KEY(external_token_on_item_id) REFERENCES token_on_item(token_on_item_id) ON DELETE CASCADE ON UPDATE CASCADE,
	
	FOREIGN KEY(token_on_item_id) REFERENCES token_on_item(token_on_item_id) ON DELETE SET NULL	
);

CREATE TABLE citation_lang_data
(
	citation_lang_data_id INTEGER PRIMARY KEY AUTOINCREMENT,
	citation_id INT NOT NULL,	-- FK from the citation_main_data table
	data_language TINYINT UNSIGNED NOT NULL default 0, -- (pre-defined numbers representing supported FTB languages, 0 = English the default )
	description text default '',
	
	-- if the referenced citation record is erased, erase all it's dependant associative records, cannot connect to invalid records
    FOREIGN KEY(citation_id) REFERENCES citation_main_data(citation_id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE TABLE media_item_main_data (
    media_item_id INTEGER PRIMARY KEY NOT NULL,  -- media item ID comes from framework
	place_id INT NULL, -- FK from the places_main_data table
	guid varchar(255) default '',
	date varchar(255) default '0000-00-00',  -- free text that can look like "22 NOV 1963" or "JUN 1940" or "BET 1953 AND 1960"
	sorted_date INT default 0, -- a number representing the date for sorting (formatted YYYYMMDD example 20140201 for 1/2/2014)
	lower_bound_search_date INT default 0, -- a number representing the date lower boundary for search (formatted YYYYMMDD example 20140201 for 1/2/2014)
	upper_bound_search_date INT default 0, -- a number representing the date lower boundary for search (formatted YYYYMMDD example 20140201 for 1/2/2014)		
	item_type tinyint unsigned NOT NULL default '0', -- predefined numbers representing the item type (image, doc, audio or video OR Personal Photo)	
	import_url varchar(255) NOT NULL default '',	
	is_privatized  tinyint UNSIGNED NOT NULL default '0',
	is_scanned_document  tinyint UNSIGNED NOT NULL default '0',
	is_hide_face_detection tinyint UNSIGNED NOT NULL default '0',
	file_size varchar(255) default '',
	file_crc varchar(255) default '',
	is_deleted_online tinyint UNSIGNED NOT NULL default '0',
	pending_download INT NOT NULL default 0,
	file varchar(255) default '',	-- file or URL where this photo needs to be downloaded
	parent_photo_id INT NOT NULL default 0,  -- if this is a personal picture, this is the ID of the picture it was taken from
	photo_file_last_modified INT NOT NULL default 0, -- Unix Time, the number of seconds since 1970-01-01 00:00:00 UTC
	reverse_photo_file_last_modified INT NOT NULL default 0, -- Unix Time, the number of seconds since 1970-01-01 00:00:00 UTC
	photo_file_id INT NOT NULL default -1,  -- the original FTB index field of this media item in case this media item is a photo, used to locate the image file
	delete_flag tinyint UNSIGNED NOT NULL default 0,  -- soft erase flag
	token_on_item_id INT NULL DEFAULT NULL,  -- FK from the token_on_item table
	
	FOREIGN KEY(place_id) REFERENCES places_main_data(place_id)  ON DELETE SET NULL,  
	FOREIGN KEY(token_on_item_id) REFERENCES token_on_item(token_on_item_id) ON DELETE SET NULL
);

CREATE TABLE media_item_lang_data (
    media_item_lang_data_id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_item_id INT NOT NULL, -- FK from the media_item_main_data table
    data_language TINYINT UNSIGNED NOT NULL default '0', -- (pre-defined numbers representing supported FTB languages, 0 = English the default )	
	title varchar(255) default '',
	description text default '',
    
    -- if the main media item record is erased, erase all it's lang records
    FOREIGN KEY(media_item_id) REFERENCES media_item_main_data(media_item_id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE TABLE media_item_to_item_connection (
    media_item_to_item_connection_id INTEGER PRIMARY KEY AUTOINCREMENT,	
    media_item_id INT NOT NULL, -- FK from the media_item_main_data table
	guid varchar(255) default '',
	delete_flag tinyint UNSIGNED default 0,  -- soft erase flag
	token_entity_id INT NOT NULL default 0, -- the entity identifier of the Genealogy entity that owns this external token
	token_item_type TINYINT UNSIGNED NOT NULL default 0, -- (the type of token this token connects to, pre-defined numbers representing supported Genealogy entity types, 0 = default no type defined)
	external_token_on_item_id INT NULL DEFAULT NULL,  -- External Token the entity this citation refers to, FK from the token_on_item table
	
    -- if the main media item record is erased, erase all it's dependant associative records, cannot connect tokens to invalid media items
    FOREIGN KEY(media_item_id) REFERENCES media_item_main_data(media_item_id) ON DELETE CASCADE ON UPDATE CASCADE,
	
    -- if the token on item is erased, erase all it's dependant associative records, cannot connect media items to invalid tokens
    FOREIGN KEY(external_token_on_item_id) REFERENCES token_on_item(token_on_item_id) ON DELETE CASCADE ON UPDATE CASCADE
);

