# Import necessary libraries
from datetime import datetime
from .json_tools import write_formatted_json  # Local import for writing JSON
from .Sessions import Session  # Local import for session handling
from pathlib import Path  # For object-oriented filesystem paths


class Concatenate:
    """
    A class to concatenate session information from a specified directory and generate a summary file.

    This class scans a directory for session folders, extracts information from each session,
    and then compiles this data into a single JSON file.
    """

    def __init__(self, name: str, session_path: str = 'sessions/', record_nodes=None):
        """
        Initializes the Concatenate object.

        Args:
            name (str): A name for the concatenation instance.
            session_path (str, optional): The path to the directory containing session folders. 
                                          Defaults to 'sessions/'.
            record_nodes (optional): A filter for specific record nodes to be included. 
                                     Passed to the Session object. Defaults to None.
        
        Raises:
            ValueError: If the provided session_path is not a valid directory.
        """
        self.name = name
        self.created_at = datetime.now().strftime("%Y%m%d%H%M")

        # Validate and store the session path
        session_path_obj = Path(session_path)
        if not session_path_obj.is_dir():
            raise ValueError(f"{session_path!r} is not a directory")
        self.session_path = session_path_obj
        print(f"Session path: {self.session_path}")

        self.record_nodes = record_nodes

        # Parse the session list to populate session information
        self._parse_session_list()

        # Set attributes based on the parsed session data
        self.session_count = len(self.session_path_list)
        self.start_session = self.session_path_list[0] if self.session_path_list else None
        self.end_session = self.session_path_list[-1] if self.session_path_list else None

    def gen_concatenate_file(self, path: str = "../"):
        """
        Generates a JSON file containing the concatenated session information.

        Args:
            path (str, optional): The path where the output JSON file will be saved. 
                                  Defaults to "../".
        """
        filename = "concatenate_file"

        # Prepare the names and content for the JSON file
        content_name_list = [
            "name", "time", "session_path", "session_count",
            "start_session", "end_session", "session_list"
        ]
        content_list = [
            self.name, self.created_at, str(self.session_path), self.session_count,
            self.start_session, self.end_session, self.session_list
        ]

        # Write the data to a formatted JSON file
        write_formatted_json(content_name_list, content_list, filename=filename, add_timestamp=True, path=path)

    def _parse_session_list(self) -> None:
        """
        Parses the session directory to extract information from each session folder.
        
        This private method iterates through the subdirectories in `self.session_path`,
        filters out hidden folders, and creates a `Session` object for each valid folder
        to extract its information.
        """
        # Get a sorted list of session directories, ignoring hidden files
        self.session_path_list = sorted([
            str(child)
            for child in self.session_path.iterdir()
            if child.is_dir() and not child.name.startswith(".")
        ])

        if not self.session_path_list:
            print("No session directories found.")
            self.session_list = []
            return

        print(f"Found session paths: {self.session_path_list}")

        # Create a list of Session objects and get their info
        self.session_list = [
            Session(session_path).get_session_info()
            for session_path in self.session_path_list
        ]
